from typing import List
import numpy as np
from tqdm import tqdm


def retrieve_knn(query_ids: List[str], key_ids: List[str], query_vecs, key_vecs, k=2047, query_batch_size=1000,
                 key_batch_size=10000):
    """
    Retrieve the top-k nearest neighbors for each query id from the key ids.
    Args:
        query_ids:
        key_ids:
        k: top-k
        query_batch_size:
        key_batch_size:

    Returns:

    """
    if len(key_vecs) == 0: return {}

    query_vecs = np.array(query_vecs, dtype=np.float32)
    query_vecs = query_vecs / np.linalg.norm(query_vecs, axis=1, keepdims=True)

    key_vecs = np.array(key_vecs, dtype=np.float32)
    key_vecs = key_vecs / np.linalg.norm(key_vecs, axis=1, keepdims=True)

    results = {}

    def get_batches(vecs, batch_size):
        for i in range(0, len(vecs), batch_size):
            yield vecs[i:i + batch_size], i

    for query_batch, query_batch_start_idx in tqdm(
            get_batches(vecs=query_vecs, batch_size=query_batch_size),
            total=(len(query_vecs) + query_batch_size - 1) // query_batch_size,
            desc="KNN for Queries"
    ):
        batch_topk_sim_scores = []
        batch_topk_indices = []

        offset_keys = 0

        for key_batch, key_batch_start_idx in get_batches(vecs=key_vecs, batch_size=key_batch_size):
            actual_key_batch_size = key_batch.shape[0]

            similarity = np.dot(query_batch, key_batch.T)

            topk_indices = np.argpartition(similarity, -min(k, actual_key_batch_size), axis=1)[:, -min(k, actual_key_batch_size):]
            topk_sim_scores = np.take_along_axis(similarity, topk_indices, axis=1)

            # Sort the top-k indices and scores
            sorted_indices = np.argsort(topk_sim_scores, axis=1)[:, ::-1]
            topk_indices = np.take_along_axis(topk_indices, sorted_indices, axis=1)
            topk_sim_scores = np.take_along_axis(topk_sim_scores, sorted_indices, axis=1)

            topk_indices += offset_keys

            batch_topk_sim_scores.append(topk_sim_scores)
            batch_topk_indices.append(topk_indices)

            offset_keys += actual_key_batch_size

        batch_topk_sim_scores = np.concatenate(batch_topk_sim_scores, axis=1)
        batch_topk_indices = np.concatenate(batch_topk_indices, axis=1)

        final_topk_indices = np.argpartition(batch_topk_sim_scores, -min(k, batch_topk_sim_scores.shape[1]), axis=1)[:, -min(k, batch_topk_sim_scores.shape[1]):]
        final_topk_sim_scores = np.take_along_axis(batch_topk_sim_scores, final_topk_indices, axis=1)

        # Sort the final top-k indices and scores
        sorted_indices = np.argsort(final_topk_sim_scores, axis=1)[:, ::-1]
        final_topk_indices = np.take_along_axis(final_topk_indices, sorted_indices, axis=1)
        final_topk_sim_scores = np.take_along_axis(final_topk_sim_scores, sorted_indices, axis=1)

        for i in range(final_topk_indices.shape[0]):
            query_relative_idx = query_batch_start_idx + i
            query_idx = query_ids[query_relative_idx]

            final_topk_indices_i = final_topk_indices[i]
            final_topk_sim_scores_i = final_topk_sim_scores[i]

            query_to_topk_key_relative_ids = batch_topk_indices[i][final_topk_indices_i]
            query_to_topk_key_ids = [key_ids[idx] for idx in query_to_topk_key_relative_ids]
            results[query_idx] = (query_to_topk_key_ids, final_topk_sim_scores_i.tolist())

    return results