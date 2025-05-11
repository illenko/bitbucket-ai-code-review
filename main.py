import os
import re
import json
import time
from pathlib import Path
import logging

import yaml
import requests
import json_repair
import tiktoken
from bitbucket_pipes_toolkit import TokenAuth
from openai import OpenAI, BadRequestError
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT_FOR_CODE_REVIEW = '''
"Review a file of source code, and the git diff of a set of changes made to that file on a Pull Request. Follow a software development principles: SOLID, DRY, KISS, YAGNI. Skip compliments. Propose corrections."
"You are a helpful assistant designed to output JSON."
"The response must be a JSON object containing summarization and suggestions where the key for each piece of feedback is the filename and line number in the file where the feedback must be left, and the value is the feedback itself as a string. "
"JSON must follow the next structure {"summary“: "{pull request detailed description}", "suggestions": { "{filename:line-number}“: “{feedback relating to the referenced line in the file.}“ } }"
'''


class BitbucketApiService:
    BITBUCKET_API_BASE_URL = "https://api.bitbucket.org/2.0"
    DIFF_DELIMITER = "diff --git a/"

    def __init__(self, auth, workspace, repo_slug):
        self.auth = auth
        self.workspace = workspace
        self.repo_slug = repo_slug

    def get_pull_request_diffs(self, pull_request_id):
        url_diff = f"{self.BITBUCKET_API_BASE_URL}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pull_request_id}/diff"
        response = requests.request("GET", url_diff, auth=self.auth)
        response.raise_for_status()

        # git diff context is too complex and contains JSON-restricted symbols to return in JSON format
        return response.text

    def add_comment(self, pull_request_id, payload):
        url_comment = f"{self.BITBUCKET_API_BASE_URL}/repositories/{self.workspace}/{self.repo_slug}/pullrequests/{pull_request_id}/comments"
        response = requests.request("POST", url_comment, auth=self.auth, json=payload)
        response.raise_for_status()

        return response.json()

    @staticmethod
    def fetch_diffs(diffs, filenames=None, delimiter=None):
        if filenames:
            return [delimiter + diff for diff in diffs.split(delimiter) for filename in filenames if
                    diff.startswith(filename)]
        else:
            return [delimiter + diff for diff in diffs.split(delimiter)]


class AiService:
    def __init__(self, base_url, api_key, organization=None, *args, **kwargs):
        self.client = OpenAI(base_url=base_url, api_key=api_key, organization=organization, *args, **kwargs)

    def create_completion(self, model, messages, **kwargs):
        completion = self.client.chat.completions.create(
            response_format={"type": "json_object"},
            model=model,
            messages=messages,
            **kwargs
        )

        return completion

    @staticmethod
    def fetch_json(data):
        return json_repair.loads(data)

    @staticmethod
    def num_tokens_from_messages(messages, model):
        """Returns the number of tokens used by a list of messages.
        Recommended way by OpenAI guides:
        https://platform.openai.com/docs/guides/text-generation/managing-tokens
        """

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")

        num_tokens = 0
        for message in messages:
            num_tokens += 4  # every message follows <im_start>{role/name}\n{content}<im_end>\n
            for key, value in message.items():
                num_tokens += len(encoding.encode(value))
                if key == "name":  # if there's a name, the role is omitted
                    num_tokens += -1  # role is always required and always 1 token
        num_tokens += 2  # every reply is primed with <im_start>assistant
        return num_tokens


class CodeReviewPipe:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.auth_method_bitbucket = self.resolve_auth()

        # Bitbucket
        self.workspace = os.getenv('BITBUCKET_WORKSPACE')
        self.repo_slug = os.getenv('BITBUCKET_REPO_SLUG')
        self.bitbucket_client = BitbucketApiService(self.auth_method_bitbucket, self.workspace, self.repo_slug)

        # ChatGPT
        self.open_api_key = os.getenv('OPENAI_API_KEY')
        self.base_url = os.getenv('OPENAI_BASE_URL')
        self.organization = os.getenv('ORGANIZATION')
        self.model = os.getenv('MODEL')
        self.user_message_content = os.getenv('MESSAGE')
        self.files_to_review = os.getenv("FILES_TO_REVIEW")
        self.completion_parameters_payload_file = os.getenv('CHATGPT_COMPLETION_FILEPATH')
        self.chatgpt_parameters_payload_file = os.getenv('CHATGPT_CLIENT_FILEPATH')
        self.chat_gpt_client = None

    @staticmethod
    def resolve_auth():
        username = os.getenv('BITBUCKET_USERNAME')
        password = os.getenv('BITBUCKET_APP_PASSWORD')
        token = os.getenv('BITBUCKET_ACCESS_TOKEN')

        if username and password:
            return HTTPBasicAuth(username, password)
        elif token:
            return TokenAuth(token)
        else:
            raise ValueError('Authentication missing. You must provide an access token or a username and app password.')

    def get_diffs_to_review(self, pull_request_id):
        diffs_text = self.bitbucket_client.get_pull_request_diffs(pull_request_id)

        files_to_review = []
        if self.files_to_review and self.files_to_review.split(','):
            files_to_review = self.files_to_review.split(',')

        return self.bitbucket_client.fetch_diffs(diffs_text, files_to_review, self.bitbucket_client.DIFF_DELIMITER)

    @staticmethod
    def get_files_with_diffs(diffs_to_review):
        # string example "diff --git a/pipe/pipe.py b/pipe/pipe.py\n..."
        pattern_filename = re.compile(r"a/(.*?) b/")
        files_with_diffs = [re.search(pattern_filename, diff).group(1) for diff in diffs_to_review if
                            re.search(pattern_filename, diff)]

        return files_with_diffs

    @staticmethod
    def load_yaml(filepath):
        if not Path(filepath).exists():
            raise FileNotFoundError(f"File {filepath} doesn't exist.")

        try:
            with open(filepath, 'r') as stream:
                return yaml.safe_load(stream)
        except yaml.YAMLError as error:
            raise yaml.YAMLError(f"File {filepath} couldn't be loaded. Error: {error}")

    def get_code_review(self, diffs_to_review):
        messages = []
        default_messages_system = {
            "role": "system",
            "content": DEFAULT_SYSTEM_PROMPT_FOR_CODE_REVIEW
        }
        messages.append(default_messages_system)

        if self.user_message_content:
            messages.append({"role": "system", "content": self.user_message_content})

        default_messages_diffs = {
            "role": "user",
            "content": str(diffs_to_review)
        }
        messages.append(default_messages_diffs)

        # count tokens
        num_tokens = self.chat_gpt_client.num_tokens_from_messages(messages, self.model)

        chat_gpt_token_limit = int(os.getenv('CHATGPT_PROMPT_MAX_TOKENS', '0'))

        if chat_gpt_token_limit != 0 and num_tokens > chat_gpt_token_limit:
            logger.warning(
                f"The number of tokens is ~{num_tokens} that more then allowed CHATGPT_PROMPT_MAX_TOKENS {chat_gpt_token_limit} tokens")
            logger.info('Pipe is stopped.')
            return None

        logger.info(f"ChatGPT configuration: model: {self.model}")

        completion_params = {
            'model': self.model,
            'messages': messages,
        }

        if self.completion_parameters_payload_file:
            users_completion_params = self.load_yaml(self.completion_parameters_payload_file)

            logger.info(f"ChatGPT configuration: completion parameters: {users_completion_params}")

            completion_params.update(users_completion_params)

        logger.info(f"ChatGPT configuration: messages: {messages}")
        logger.info("Processing ChatGPT...")

        start_time = time.time()
        completion = None
        try:
            completion = self.chat_gpt_client.create_completion(**completion_params)
        except BadRequestError as error:
            raise error

        end_time = time.time()

        logger.debug(completion)
        logger.info(f"Processing ChatGPT takes: {round(end_time - start_time)} seconds")
        logger.info(f'ChatGPT completion tokens: {completion.usage}')

        raw_suggestions = completion.choices[0].message.content

        logger.debug(raw_suggestions)

        suggestions = None
        try:
            suggestions = self.chat_gpt_client.fetch_json(raw_suggestions)
        except json.JSONDecodeError as error:
            raise error

        logger.debug(suggestions)

        return suggestions

    def add_comments(self, pull_request_id, data):
        pattern_filename_line = re.compile(r"(.+):(\d+)")

        added_suggestions_counter = 0
        files_with_comments = []

        for filename_line, content in data.items():
            filename_line_match = re.match(pattern_filename_line, filename_line)
            if filename_line_match and len(content):
                filename, line = filename_line_match.groups()
                payload = {
                    'inline': {
                        'to': int(line),
                        'path': filename
                    },
                    'content': {
                        'raw': content
                    }
                }
                self.bitbucket_client.add_comment(pull_request_id, payload)

                files_with_comments.append(filename)
                added_suggestions_counter += 1

        return set(files_with_comments), added_suggestions_counter

    def add_summary(self, pull_request_id, summary):
        payload = {
            'content': {
                'raw': summary
            }
        }
        self.bitbucket_client.add_comment(pull_request_id, payload)

    def run(self):
        logger.info('Executing the pipe...')

        pull_request_id = os.getenv("BITBUCKET_PR_ID")
        if pull_request_id is None:
            raise EnvironmentError('BITBUCKET_PR_ID variable is required!')

        diffs_to_review = self.get_diffs_to_review(pull_request_id)

        logger.debug(diffs_to_review)

        if not diffs_to_review:
            logger.warning(f"No files for codereview. Check configuration in FILES_TO_REVIEW: {self.files_to_review}")
            return

        files_with_diffs = self.get_files_with_diffs(diffs_to_review)

        logger.info(f"Files with diffs count {len(files_with_diffs)}: {set(files_with_diffs)}")

        chatgpt_parameters = {
            "base_url": self.base_url,
            "api_key": self.open_api_key,
            "organization": self.organization,
        }

        if self.chatgpt_parameters_payload_file:
            users_chatgpt_parameters = self.load_yaml(self.chatgpt_parameters_payload_file)

            logger.info(f"ChatGPT configuration: client parameters: {users_chatgpt_parameters}")

            chatgpt_parameters.update(users_chatgpt_parameters)

        self.chat_gpt_client = AiService(**chatgpt_parameters)

        code_review = self.get_code_review(diffs_to_review)
        summary = code_review.get('summary')
        suggestions = code_review.get('suggestions')
        if summary:
            self.add_summary(pull_request_id, summary)
        files_with_comments, added_suggestions_counter = self.add_comments(pull_request_id, suggestions)

        logger.info(f"ChatGPT suggestions count: {added_suggestions_counter}")
        logger.info(f'Commented files count {len(files_with_comments)}: {files_with_comments}')

        ui_pull_request_url = f"https://bitbucket.org/{self.workspace}/{self.repo_slug}/pull-requests/{pull_request_id}"
        logger.info(f"Successfully added the comments provided by ChatGPT to the pull request: {ui_pull_request_url}")
        return


if __name__ == '__main__':
    pipe = CodeReviewPipe()
    pipe.run()