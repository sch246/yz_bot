from copy import deepcopy
from typing import List, Optional
import os

import numpy as np
from tqdm import tqdm
from openai import OpenAI

from ..utils.config_utils import BaseConfig
from ..utils.logging_utils import get_logger
from .base import BaseEmbeddingModel, EmbeddingConfig, make_cache_embed

logger = get_logger(__name__)

class OpenAIEmbeddingModel(BaseEmbeddingModel):

    def __init__(self, global_config: Optional[BaseConfig] = None, embedding_model_name: Optional[str] = None) -> None:
        super().__init__(global_config=global_config)

        if embedding_model_name is not None:
            self.embedding_model_name = embedding_model_name
            logger.debug(
                f"Overriding {self.__class__.__name__}'s embedding_model_name with: {self.embedding_model_name}")

        self._init_embedding_config()

        # 初始化嵌入模型
        logger.debug(
            f"Initializing {self.__class__.__name__}'s embedding model with params: {self.embedding_config.model_init_params}")

        # 使用OpenAI API
        api_key = self.global_config.embedding_api_key
        if api_key is None:
            api_key = os.getenv("OPENAI_API_KEY")
            
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.global_config.embedding_base_url,
        )
            
        # 设置向量维度，对不同的模型有不同的值
        if 'text-embedding' in self.embedding_model_name:
            if 'ada' in self.embedding_model_name:
                self.embedding_dim = 1536  # text-embedding-ada-002
            else:
                self.embedding_dim = 3072  # text-embedding-3-small及以上
        elif 'bge-m3' in self.embedding_model_name:
            self.embedding_dim = 1024  # BAAI/bge-m3
        else:
            # 默认维度
            self.embedding_dim = 1536

    def _init_embedding_config(self) -> None:
        """
        Extract embedding model-specific parameters to init the EmbeddingConfig.

        Returns:
            None
        """

        config_dict = {
            "embedding_model_name": self.embedding_model_name,
            "norm": self.global_config.embedding_return_as_normalized,
            "model_init_params": {
                "pretrained_model_name_or_path": self.embedding_model_name,
                "trust_remote_code": True,
            },
            "encode_params": {
                "max_length": self.global_config.embedding_max_seq_len,
                "instruction": "",
                "batch_size": self.global_config.embedding_batch_size,
                "num_workers": 32
            },
        }

        self.embedding_config = EmbeddingConfig.from_dict(config_dict=config_dict)
        logger.debug(f"Init {self.__class__.__name__}'s embedding_config: {self.embedding_config}")

    def encode(self, texts: List[str]):
        texts = [t.replace("\n", " ") for t in texts]
        texts = [t if t != '' else ' ' for t in texts]
        response = self.client.embeddings.create(input=texts, model=self.embedding_model_name)
        results = np.array([v.embedding for v in response.data])

        return results

    def batch_encode(self, texts: List[str], **kwargs) -> None:
        if isinstance(texts, str): texts = [texts]

        params = deepcopy(self.embedding_config.encode_params)
        if kwargs: params.update(kwargs)

        if "instruction" in kwargs:
            if kwargs["instruction"] != '':
                params["instruction"] = f"Instruct: {kwargs['instruction']}\nQuery: "

        logger.debug(f"Calling {self.__class__.__name__} with:\n{params}")

        batch_size = params.pop("batch_size", 16)

        if len(texts) <= batch_size:
            results = self.encode(texts)
        else:
            pbar = tqdm(total=len(texts), desc="Batch Encoding")
            results = []
            for i in range(0, len(texts), batch_size):
                batch = texts[i:i + batch_size]
                try:
                    results.append(self.encode(batch))
                except:
                    import ipdb; ipdb.set_trace()
                pbar.update(batch_size)
            pbar.close()
            results = np.concatenate(results)

        if self.embedding_config.norm:
            results = (results.T / np.linalg.norm(results, axis=1)).T

        return results
