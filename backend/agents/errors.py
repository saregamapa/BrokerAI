"""Errors raised by the LangGraph campaign pipeline when OpenAI or media generation fails."""


class CampaignPipelineError(RuntimeError):
    """Campaign generation cannot continue (missing OpenAI, API failure, invalid output)."""


class OpenAINotConfiguredError(CampaignPipelineError):
    """OPENAI_API_KEY is missing or a placeholder value."""
