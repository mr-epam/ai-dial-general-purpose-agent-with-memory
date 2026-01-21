import os
os.environ['OMP_NUM_THREADS'] = '1'

import json
from datetime import datetime, UTC, timedelta
import numpy as np
import faiss
from aidial_client import AsyncDial
from sentence_transformers import SentenceTransformer

from task.tools.memory._models import Memory, MemoryData, MemoryCollection


class LongTermMemoryStore:
    """
    Manages long-term memory storage for users.

    Storage format: Single JSON file per user in DIAL bucket
    - File: {user_id}/long-memories.json
    - Caching: In-memory cache with conversation_id as key
    - Deduplication: O(n log n) using FAISS batch search
    """

    DEDUP_INTERVAL_HOURS = 24

    def __init__(self, endpoint: str):
        # 1. Set endpoint
        self.endpoint = endpoint
        
        # 2. Create SentenceTransformer as model, model name is `all-MiniLM-L6-v2`
        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
        
        # 3. Create cache, dict of str and MemoryCollection (it is imitation of cache, normally such cache should be set aside)
        self.cache: dict[str, MemoryCollection] = {}
        
        # 4. Make `faiss.omp_set_num_threads(1)` (without this set up you won't be able to work in debug mode in `_deduplicate_fast` method
        faiss.omp_set_num_threads(1)

    async def _get_memory_file_path(self, dial_client: AsyncDial) -> str:
        """Get the path to the memory file in DIAL bucket."""
        # 1. Get DIAL app home path
        files_home = await dial_client.my_appdata_home()
        
        # 2. Return string with path in such format: `files/{bucket_with_app_home}/__long-memories/data.json`
        #    The memories will persist in appdata for this agent in `__long-memories` folder and `data.json` file
        #    (You will be able to check it also in Chat UI in attachments)
        memory_path = files_home / "__long-memories" / "data.json"
        return f"files/{memory_path.as_posix()}"

    async def _load_memories(self, api_key: str) -> MemoryCollection:
        # 1. Create AsyncDial client (api_version is 2025-01-01-preview)
        dial_client = AsyncDial(
            base_url=self.endpoint,
            api_key=api_key,
            api_version='2025-01-01-preview'
        )
        
        # 2. Get memory file path
        memory_file_path = await self._get_memory_file_path(dial_client)
        
        # 3. Check cache: cache is dict of str and MemoryCollection, for the key we will use `memory file path` to make
        #    it simple. Such key will be unique for user and will allow to access memories across different
        #    conversations and only user can access them. In case if cache is present return its MemoryCollection.
        if memory_file_path in self.cache:
            return self.cache[memory_file_path]
        
        # ---
        # Below is logic when cache is not present:
        # 4. Open try-except block:
        try:
            #   - in try:
            #       - download file content
            file_download_response = await dial_client.files.download(memory_file_path)
            #       - in response get content and decode it with 'utf-8'
            file_content = file_download_response.get_content().decode('utf-8')
            #       - load content with `json`
            file_data = json.loads(file_content)
            #       - create MemoryCollection (it is pydentic model, use `model_validate` method)
            memory_collection = MemoryCollection.model_validate(file_data)
        except Exception:
            #   - in except:
            #       - create MemoryCollection (it will have empty memories, set up time for updated_at, more detailed take
            #         a look at MemoryCollection pydentic model and it Fields)
            memory_collection = MemoryCollection(
                memories=[],
                updated_at=datetime.now(UTC),
                last_deduplicated_at=None
            )
        
        # 5. Return created MemoryCollection
        # Store in cache before returning
        self.cache[memory_file_path] = memory_collection
        return memory_collection

    async def _save_memories(self, api_key: str, memories: MemoryCollection):
        """Save memories to DIAL bucket and update cache."""
        # 1. Create AsyncDial client
        dial_client = AsyncDial(
            base_url=self.endpoint,
            api_key=api_key,
            api_version='2025-01-01-preview'
        )
        
        # 2. Get memory file path
        memory_file_path = await self._get_memory_file_path(dial_client)
        
        # 3. Update `updated_at` of memories (now)
        memories.updated_at = datetime.now(UTC)
        
        # 4. Converts memories to json string (it's pydentic model and it have model dump json method for this). Don't
        #    make any indentations because it will make file 'bigger'. Here is the point that we store all the memories
        #    in one file and 'one memory' with its embeddings takes ~6-8Kb, we expect that there are won't be more that
        #    1000 memories but anyway for 1000 memories it will be ~6-8Mb, so, we need to make at least these small
        #    efforts to make it smaller 😉
        json_string = memories.model_dump_json()
        file_data = json_string.encode('utf-8')
        
        # Upload file
        await dial_client.files.upload(url=memory_file_path, file=file_data)
        
        # 5. Put to cache (kind reminder the key is memory file path)
        self.cache[memory_file_path] = memories

    async def add_memory(self, api_key: str, content: str, importance: float, category: str, topics: list[str]) -> str:
        """Add a new memory to storage."""
        # 1. Load memories
        memory_collection = await self._load_memories(api_key)
        
        # 2. Make encodings for content with embedding model.
        #    Hint: provide content as list, and after encoding get first result (encode wil return list) and convertit `tolist`
        embedding = self.embedding_model.encode([content])[0].tolist()
        
        # 3. Create Memory
        #    - for id use `int(datetime.now(UTC).timestamp())` it will provide time now as int, it will be super enough
        #      to avoid collisions. Also, we won't use id but we added it because maybe in future you will make enhanced
        #      version of long-term memory and after that it will be additional 'headache' to add such ids 😬
        memory_id = int(datetime.now(UTC).timestamp())
        memory_data = MemoryData(
            id=memory_id,
            content=content,
            importance=importance,
            category=category,
            topics=topics
        )
        memory = Memory(data=memory_data, embedding=embedding)
        
        # 4. Add to memories created memory
        memory_collection.memories.append(memory)
        
        # 5. Save memories (it is PUT request bzw, -> https://dialx.ai/dial_api#tag/Files/operation/uploadFile)
        await self._save_memories(api_key, memory_collection)
        
        # 6. Return information that content has benn successfully stored
        return f"Memory successfully stored: {content}"

    async def search_memories(self, api_key: str, query: str, top_k: int = 5) -> list[MemoryData]:
        """
        Search memories using semantic similarity.

        Returns:
            List of MemoryData objects (without embeddings)
        """
        # 1. Load memories
        memory_collection = await self._load_memories(api_key)
        
        # 2. If they are empty return empty array
        if not memory_collection.memories:
            return []
        
        # ---
        # 3. Check if they needs_deduplication, if yes then deduplicate_and_save (need to implements both of these methods)
        if self._needs_deduplication(memory_collection):
            memory_collection = await self._deduplicate_and_save(api_key, memory_collection)
        
        # 4. Make vector search (embeddings are part of memory)😈
        # Encode query
        query_embedding = self.embedding_model.encode([query])[0]
        
        # Build FAISS index
        dimension = len(query_embedding)
        index = faiss.IndexFlatIP(dimension)  # Inner product for cosine similarity (after normalization)
        
        # Normalize embeddings and add to index
        embeddings_array = np.array([memory.embedding for memory in memory_collection.memories], dtype=np.float32)
        faiss.normalize_L2(embeddings_array)  # Normalize for cosine similarity
        index.add(embeddings_array)
        
        # Search
        query_embedding_np = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query_embedding_np)
        distances, indices = index.search(query_embedding_np, min(top_k, len(memory_collection.memories)))
        
        # 5. Return `top_k` MemoryData based on vector search
        results = []
        for idx in indices[0]:
            if idx < len(memory_collection.memories):
                results.append(memory_collection.memories[idx].data)
        
        return results

    def _needs_deduplication(self, collection: MemoryCollection) -> bool:
        """Check if deduplication is needed (>24 hours since last deduplication)."""
        # The criteria for deduplication (collection length > 10 and >24 hours since last deduplication) or
        # (collection length > 10 last deduplication is None)
        if len(collection.memories) <= 10:
            return False
        
        if collection.last_deduplicated_at is None:
            return True
        
        time_since_dedup = datetime.now(UTC) - collection.last_deduplicated_at
        return time_since_dedup > timedelta(hours=self.DEDUP_INTERVAL_HOURS)

    async def _deduplicate_and_save(self, api_key: str, collection: MemoryCollection) -> MemoryCollection:
        """
        Deduplicate memories synchronously and save the result.
        Returns the updated collection.
        """
        # 1. Make fast deduplication (need to implement)
        deduplicated_memories = self._deduplicate_fast(collection.memories)
        collection.memories = deduplicated_memories
        
        # 2. Update last_deduplicated_at as now
        collection.last_deduplicated_at = datetime.now(UTC)
        
        # 3. Save deduplicated memories
        await self._save_memories(api_key, collection)
        
        # 4. Return deduplicated collection
        return collection

    def _deduplicate_fast(self, memories: list[Memory]) -> list[Memory]:
        """
        Fast deduplication using FAISS batch search with cosine similarity.

        Strategy:
        - Find k nearest neighbors for each memory using cosine similarity
        - Mark duplicates based on similarity threshold (cosine similarity > 0.75)
        - Keep memory with higher importance
        """
        # This is the hard part 🔥🔥🔥
        # You need to deduplicate memories, duplicates are the memories that have 75% similarity.
        # Among duplicates remember about `importance`, most important have more priorities to survive
        # It must be fast, it is possible to do for O(n log n), probably you can find faster way (share with community if do 😉)
        
        if len(memories) <= 1:
            return memories
        
        # Build FAISS index
        dimension = len(memories[0].embedding)
        index = faiss.IndexFlatIP(dimension)  # Inner product for cosine similarity
        
        # Normalize embeddings and add to index
        embeddings_array = np.array([memory.embedding for memory in memories], dtype=np.float32)
        faiss.normalize_L2(embeddings_array)  # Normalize for cosine similarity
        index.add(embeddings_array)
        
        # Find duplicates: for each memory, find neighbors with similarity > 0.75
        similarity_threshold = 0.75
        to_remove = set()
        
        # Search for k nearest neighbors (including itself, so k=2 minimum)
        k = min(len(memories), 10)  # Check up to 10 nearest neighbors
        distances, indices = index.search(embeddings_array, k)
        
        # Process each memory and its neighbors
        for i in range(len(memories)):
            if i in to_remove:
                continue
            
            current_importance = memories[i].data.importance
            
            # Check neighbors (skip the first one as it's the memory itself)
            for j, neighbor_idx in enumerate(indices[i][1:], start=1):
                if neighbor_idx in to_remove:
                    continue
                
                similarity = distances[i][j]
                if similarity >= similarity_threshold:
                    neighbor_importance = memories[neighbor_idx].data.importance
                    
                    # Keep the one with higher importance, remove the other
                    if neighbor_importance > current_importance:
                        to_remove.add(i)
                        break  # Current memory is removed, no need to check more neighbors
                    else:
                        to_remove.add(neighbor_idx)
        
        # Return deduplicated memories
        return [memory for idx, memory in enumerate(memories) if idx not in to_remove]

    async def delete_all_memories(self, api_key: str, ) -> str:
        """
        Delete all memories for the user.

        Removes the memory file from DIAL bucket and clears the cache
        for the current conversation.
        """
        # 1. Create AsyncDial client
        dial_client = AsyncDial(
            base_url=self.endpoint,
            api_key=api_key,
            api_version='2025-01-01-preview'
        )
        
        # 2. Get memory file path
        memory_file_path = await self._get_memory_file_path(dial_client)
        
        # 3. Delete file
        try:
            await dial_client.files.delete(memory_file_path)
        except Exception:
            # File might not exist, which is fine
            pass
        
        # Clear cache
        if memory_file_path in self.cache:
            del self.cache[memory_file_path]
        
        # 4. Return info about successful memory deletion
        return "All memories have been successfully deleted."
