from .base import EmbeddingConfig, BaseEmbeddingModel
from .OpenAI import OpenAIEmbeddingModel

from ..utils.logging_utils import get_logger

logger = get_logger(__name__)


def _get_embedding_model_class(embedding_model_name: str = "text-embedding-3-small"):
    # 默认使用OpenAI兼容API
    return OpenAIEmbeddingModel