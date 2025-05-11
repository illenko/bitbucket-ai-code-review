# BitBucket AI Code Review for CI Pipelines

This component is a Docker-based code review pipeline to provide automated code reviews using either OpenAI's GPT or Google's Gemini AI models. It's adapted from the [Bitbucket Pipeline for ChatGPT code reviews](https://bitbucket.org/atlassian/bitbucket-chatgpt-codereview/src/master/).

## Features

- Automated code review for pull requests
- Support for multiple AI providers:
    - OpenAI GPT models
    - Google Gemini
- Customizable review focus through file filtering
- Token limit management
- Detailed logging
- JSON-formatted review comments
- Bitbucket API integration

## Prerequisites

- Bitbucket repository access
- AI Provider API key (OpenAI or Google)
- Docker

## Environment Variables

### Required Variables

- `BITBUCKET_PR_ID`: Pull request ID (automatically set in PR builds)
- `BITBUCKET_WORKSPACE`: Your Bitbucket workspace name
- `BITBUCKET_REPO_SLUG`: Your repository slug

### Authentication (One of the following)

- `BITBUCKET_ACCESS_TOKEN`: Bitbucket access token
- OR
    - `BITBUCKET_USERNAME`: Bitbucket username
    - `BITBUCKET_APP_PASSWORD`: Bitbucket app password

### AI Configuration

- `OPENAI_API_KEY`: Your OpenAI API key
- `OPENAI_BASE_URL`: API base URL (can be modified for Gemini)
- `MODEL`: AI model to use
- `ORGANIZATION`: (Optional) Organization ID
- `CHATGPT_PROMPT_MAX_TOKENS`: Maximum tokens limit (defaults to 0 if not set)

### Optional Configuration

- `MESSAGE`: Custom system message for the AI
- `FILES_TO_REVIEW`: Comma-separated list of files to review
- `CHATGPT_COMPLETION_FILEPATH`: Path to YAML file with completion parameters
- `CHATGPT_CLIENT_FILEPATH`: Path to YAML file with client parameters

## Error Handling

The pipeline includes error handling for:
- Missing authentication
- Token limit exceeded
- Invalid YAML configurations
- API errors
- JSON parsing errors

## Logging

Detailed logging is available for:
- Configuration details
- Processing time
- Token usage
- Files reviewed
- Number of suggestions

## Create docker image:

```shell
docker build -t ai-code-reviewer .
```

## Run docker image:

```shell
docker run ai-code-reviewer
```
