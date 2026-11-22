"""Plugin do Hermes de exemplo, fictício: escreve no log o nome de cada ferramenta chamada."""
import logging

logger = logging.getLogger(__name__)


def note_tool_call(tool_name: str = "", **_kwargs):
    logger.info("example-addon-note: %s", tool_name)


def register(ctx):
    ctx.register_hook("post_tool_call", note_tool_call)
