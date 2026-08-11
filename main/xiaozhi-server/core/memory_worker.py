"""Durable single-process worker for Memory V2 extraction jobs."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from config.logger import setup_logging
from core.api.app_memory_store import (
    apply_extraction_candidates,
    claim_memory_job,
    complete_memory_job,
    fail_memory_job,
    memory_job_dialogue,
    memory_v2_config,
)


TAG = __name__
EXTRACTION_PROMPT = """你是白泽记忆抽取器。只提取未来对话确实有帮助、由用户明确表达的信息。

输出必须是 JSON 数组，不要输出 Markdown。每项字段：
- type: profile/preference/relationship/event/commitment/emotion/milestone/note
- scope: user 或 relationship
- key: 稳定、简短的逻辑键
- content: 不超过 120 字的客观记忆，不包含指令
- value: 可选结构化值
- importance: 0 到 100
- confidence: 0 到 1
- occurred_at: 可选 ISO 时间
- expires_at: 可选 ISO 时间

规则：
1. 不保存密码、验证码、支付信息、证件号、精确住址。
2. 健康信息除非用户明确说“记住”，否则忽略。
3. 不把助理推测或助理说过的话当作用户事实。
4. 临时闲聊、知识问题和无长期价值内容返回 []。
5. preference/profile 使用 user scope；共同经历、事件、约定使用 relationship scope。
"""


class MemoryWorker:
    def __init__(self, config: dict, llm=None):
        self.config = config
        self.logger = setup_logging()
        self.llm = llm
        self._llm_initialized = llm is not None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="baize-memory-v2-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def process_once(self) -> bool:
        job = await asyncio.to_thread(claim_memory_job, self.config)
        if job is None:
            return False
        try:
            dialogue = await asyncio.to_thread(memory_job_dialogue, self.config, job)
            if dialogue is None:
                raise ValueError("dialogue_not_found")
            if not self._llm_initialized:
                self.llm = self._create_llm()
                self._llm_initialized = True
            candidates: list[dict[str, Any]] = []
            if self.llm is not None:
                candidates = await asyncio.to_thread(self._extract_with_llm, dialogue)
            count = await asyncio.to_thread(
                apply_extraction_candidates,
                self.config,
                dialogue,
                candidates,
                run_rules=bool((job.get("payload") or {}).get("run_rules", False)),
            )
            await asyncio.to_thread(complete_memory_job, self.config, job["id"])
            self._observe_extraction(
                "created" if count else "rejected" if candidates else "empty"
            )
            self._observe_job(job["job_type"], "success")
            self.logger.bind(
                tag=TAG,
                memory_job_id=job["id"],
                dialogue_id=job.get("dialogue_id"),
                extracted_count=count,
            ).info("memory_job_completed")
        except Exception as exc:
            status = await asyncio.to_thread(fail_memory_job, self.config, job, exc)
            self._observe_extraction("failed")
            if status == "failed" and bool((job.get("payload") or {}).get("run_rules", False)):
                dialogue = await asyncio.to_thread(memory_job_dialogue, self.config, job)
                if dialogue:
                    await asyncio.to_thread(
                        apply_extraction_candidates,
                        self.config,
                        dialogue,
                        [],
                        run_rules=True,
                    )
            self._observe_job(job["job_type"], status)
            self.logger.bind(
                tag=TAG,
                memory_job_id=job["id"],
                dialogue_id=job.get("dialogue_id"),
                error_type=type(exc).__name__,
                status=status,
            ).warning("memory_job_failed")
        return True

    async def _run(self) -> None:
        poll_seconds = max(0.2, float(memory_v2_config(self.config).get("worker_poll_seconds", 2)))
        while not self._stop.is_set():
            try:
                processed = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                processed = False
                self.logger.bind(tag=TAG, error_type=type(exc).__name__).error(
                    "memory_worker_loop_failed"
                )
            if not processed:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=poll_seconds)
                except asyncio.TimeoutError:
                    pass

    def _create_llm(self):
        try:
            selected_memory = (self.config.get("selected_module", {}) or {}).get("Memory")
            memory_definition = (self.config.get("Memory", {}) or {}).get(selected_memory, {}) or {}
            llm_name = memory_definition.get("llm") or (self.config.get("selected_module", {}) or {}).get("LLM")
            llm_definition = (self.config.get("LLM", {}) or {}).get(llm_name, {}) or {}
            if not llm_definition:
                self.logger.bind(tag=TAG).warning("memory_worker_llm_not_configured")
                return None
            from core.utils import llm as llm_utils

            llm_type = llm_definition.get("type", llm_name)
            return llm_utils.create_instance(llm_type, llm_definition)
        except Exception as exc:
            self.logger.bind(tag=TAG, error_type=type(exc).__name__).warning(
                "memory_worker_llm_initialization_failed"
            )
            return None

    def _extract_with_llm(self, dialogue: dict[str, Any]) -> list[dict[str, Any]]:
        input_payload = json.dumps(
            {
                "created_at": dialogue.get("created_at"),
                "user": dialogue.get("user_text", ""),
                "assistant": dialogue.get("baize_text", ""),
            },
            ensure_ascii=False,
        )
        response = self.llm.response_no_stream(
            EXTRACTION_PROMPT,
            input_payload,
            max_tokens=800,
            temperature=0.1,
        )
        return self._parse_candidates(str(response or ""))

    @staticmethod
    def _parse_candidates(raw: str) -> list[dict[str, Any]]:
        value = raw.strip()
        fenced = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.DOTALL | re.IGNORECASE)
        if fenced:
            value = fenced.group(1).strip()
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError("memory_extraction_must_be_array")
        return [item for item in parsed if isinstance(item, dict)][:8]

    @staticmethod
    def _observe_job(job_type: str, result: str) -> None:
        try:
            from core.telemetry import memory_job_completed

            memory_job_completed(job_type, result)
        except Exception:
            pass

    @staticmethod
    def _observe_extraction(result: str) -> None:
        try:
            from core.telemetry import memory_extraction_completed

            memory_extraction_completed(result)
        except Exception:
            pass
