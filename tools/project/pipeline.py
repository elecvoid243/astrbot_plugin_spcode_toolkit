"""Project load 流水线 + ProjectLoadAbort 信号异常(PR-7 2026-06-23)。

设计:
- ProjectLoadAbort 继承 BaseException(非 Exception),
  避免被 ``except Exception`` 兜底误吞
- 子步骤包装器 ``project_load_step`` 透传子 generator 的消息,
  任何 "❌" 开头的消息视为失败,失败时 yield 总结 + 抛 ProjectLoadAbort
- 调用方: ProjectManager.load_impl 用 ``try/except ProjectLoadAbort`` 接住
"""

from __future__ import annotations

from typing import AsyncIterator

from astrbot.api.event import AstrMessageEvent


class ProjectLoadAbort(BaseException):
    """私有信号异常,用以中止 project load 流程。

    为什么用 ``BaseException`` 而非 ``Exception``?
        子方法(``self.agentsmd.init`` 等)和 helpers 内部有大量
        ``except Exception`` 兜底。用 ``BaseException`` 可避免该异常
        被这些 ``except`` 误吞,确保中止信号一定能传到顶层。

    捕获方: :meth:`ProjectManager.load_impl` 的 ``try/except ProjectLoadAbort``
    块,捕获后 ``return`` 即可。
    """

    def __init__(self, step_label: str) -> None:
        self.step_label = step_label
        super().__init__(step_label)


async def project_load_step(
    event: AstrMessageEvent,
    sub_gen: AsyncIterator[str],
    step_label: str,
) -> AsyncIterator[str]:
    """Forward messages from a sub-step; abort on first "❌" message.

    用作 :meth:`ProjectManager.load_impl` 中所有 4 个子步骤的统一包装层:

    - **透传**: ``sub_gen`` 产出的每条消息都原样 ``yield`` 出去
    - **检测**: 任何以 ``"❌"`` 开头的消息视为失败
    - **中止**: 失败时 yield 一条总结消息,然后抛 :class:`ProjectLoadAbort`
      终止整个 ``load_impl`` 流程(stop at first error)

    为什么用异常而不是 flag?
        ``load_impl`` 自己也是 async generator, ``return`` 只能
        终止自身;无法从 ``async for`` 循环内部跳出整个流。抛出一个私有
        异常是最干净的方式 — 父函数用 ``try/except ProjectLoadAbort``
        接住后直接 ``return`` 即可。

    为什么"❌"而不是返回值?
        子方法(``self.agentsmd.init`` / ``self.agentsmd.load`` /
        ``self.codegraph.init`` / ``self.codegraph.set_project``)都
        遵循 "yield 错误消息 + return" 模式,从不抛异常。``❌`` 前缀是
        它们的统一约定。``⚠️`` 不算失败 — ``codegraph.init`` 在
        "已初始化 → 自动 --force 重试" 路径上以 ``⚠️`` 起头但最终可能成功。

    Args:
        event: AstrBot 事件对象(用于 yield abort 总结消息)。
        sub_gen: 子方法返回的 async generator,**不消耗**,只在这里转发。
        step_label: 本步的人类可读标签,如 ``"[1/3] AGENTS.md 加载"``。

    Yields:
        ``sub_gen`` 的全部消息 + (若失败) 一条 abort 总结消息。

    Raises:
        ProjectLoadAbort: ``sub_gen`` 至少 yield 过一次以 ``"❌"``
            开头的消息。调用方应捕获并 ``return``。
    """
    failed = False
    async for msg in sub_gen:
        yield msg
        # msg 在生产里是 ``MessageEventResult``(由 ``event.plain_result()``
        # 返回),在单元测试里 mock 可能直接 yield 字符串。这里做"防御式"
        # 抽取,主路径(MER)走 ``.chain[0].text``;测试/mock 路径走 str。
        text: str | None = None
        if isinstance(msg, str):
            text = msg
        else:
            chain = getattr(msg, "chain", None)
            if chain:
                first = chain[0]
                text = getattr(first, "text", None)
        if isinstance(text, str) and text.startswith("❌"):
            failed = True
    if failed:
        yield event.plain_result(
            f"❌ {step_label} 失败,/project load 中止。"
            "请根据上方错误信息修复后,重试 /project load <directory>。"
        )
        raise ProjectLoadAbort(step_label)
