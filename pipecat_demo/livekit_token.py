"""LiveKit access tokens for the bot and the browser.

Thin wrappers over pipecat's own token helpers (`pipecat.runner.livekit`) so the JWT
grants stay whatever pipecat expects -- notably the `agent=True` grant on the bot's
token, which is how LiveKit clients learn that the participant is an agent. Only the
credential source differs: this demo's config instead of pipecat's env-var lookup.
"""
from pipecat.runner.livekit import generate_token, generate_token_with_agent

from pipecat_demo import config

BOT_IDENTITY = "pipecat-bot"
USER_IDENTITY = "web-user"


def generate_bot_token(room_name: str, identity: str = BOT_IDENTITY) -> str:
    """Mint the agent token the bot joins with."""
    return generate_token_with_agent(
        room_name, identity, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET
    )


def generate_user_token(room_name: str, identity: str = USER_IDENTITY) -> str:
    """Mint a plain participant token for the browser."""
    return generate_token(
        room_name, identity, config.LIVEKIT_API_KEY, config.LIVEKIT_API_SECRET
    )
