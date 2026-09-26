"""Turn a parsed character card + recalled memories into an OpenAI messages list.

Only the fields allowed by the spec are injected; creator metadata is excluded.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from scene import current_scene, parse_msg_time

from config import get_settings
from context_budget import Fragment, Priority, assemble_with_budget


# Subset of fields that the spec explicitly forbids from being injected.
_INJECT_FIELDS = (
    "description",
    "personality",
    "scenario",
    "first_mes",
    "mes_example",
    "system_prompt",
    "post_history_instructions",
    "alternate_greetings",
    "extensions",
)


def _render_placeholders(text: str, *, char_name: str, user_name: str,
                         ai_role: str = "", user_role: str = "",
                         world_setting: str = "", story_rules: str = "",
                         user_msg: str = "", ai_reply: str = "",
                         prev_scene: str = "") -> str:
    if not text:
        return ""
    return (
        text.replace("{{char}}", char_name)
        .replace("{{user}}", user_name)
        .replace("{{ai_role}}", ai_role)
        .replace("{{user_role}}", user_role)
        .replace("{{world_setting}}", world_setting)
        .replace("{{story_rules}}", story_rules)
        .replace("{{user_msg}}", user_msg)
        .replace("{{ai_reply}}", ai_reply)
        .replace("{{prev_scene}}", prev_scene)
    )


def _take_recent_turns(history: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Take the last N complete user→assistant turns from a chronological history.

    A *turn* is one user message plus its assistant reply. So N=10 yields
    at most 20 messages. Two reasons the returned slice may be shorter:

    - If the slice would otherwise open with an assistant message (because
      the persisted history happens to start mid-turn), the leading
      assistant is dropped so the LLM never sees a reply with no preceding
      question.
    - If the trailing user message has no assistant reply yet (the user
      just sent it and the model is responding now), it's still kept —
      it's exactly the message build_messages is about to answer.

    Entries with role outside ``("user", "assistant")`` are silently
    dropped. They aren't part of the conversation flow and would only
    confuse the model; logging them here would also be noise given that
    the schema has no CHECK constraint.
    """
    if n <= 0:
        return []
    cleaned = [m for m in history if m.get("role") in ("user", "assistant")]
    # Drop a leading assistant so the slice always opens with a user turn.
    while cleaned and cleaned[0].get("role") != "user":
        cleaned.pop(0)
    # Walk from the tail backwards counting user messages. The N-th-from-
    # end user message marks the start of the slice.
    keep_from = 0
    user_count = 0
    for i in range(len(cleaned) - 1, -1, -1):
        if cleaned[i].get("role") == "user":
            user_count += 1
            if user_count == n:
                keep_from = i
                break
    return cleaned[keep_from:]


def annotate_history_scenes(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给历史消息动态注入时空标注。

    仅当相邻消息跨越场景边界（或跨日）时，在该条消息内容前插入
    ``[场景 · MM-DD HH:MM]`` 标注，让 LLM 感知时空推移（昨晚在家聊、
    今早在公司回……）。时空未变时不重复标注，token 开销最小。
    """
    out: list[dict[str, Any]] = []
    prev_key: str | None = None
    prev_day: Any = None
    for m in history:
        dt = parse_msg_time(m.get("created_at"))
        scene = current_scene(dt) if dt else None
        if scene and dt and (scene.key != prev_key or (prev_day is not None and dt.date() != prev_day)):
            stamp = f"[{scene.name} · {dt.strftime('%m-%d %H:%M')}]\n"
            m = {**m, "content": f"{stamp}{m.get('content', '')}"}
        out.append(m)
        if scene:
            prev_key = scene.key
        if dt:
            prev_day = dt.date()
    return out


_CARD_FIELD_SECTIONS = (
    ("basic_info", "基本信息"),
    ("personality", "性格"),
    ("love_values", "恋爱观"),
    ("background", "身份背景"),
    ("habits", "喜好习惯"),
    ("speech_style", "说话风格"),
    ("mes_example", "示例对话"),
)


def _card_system_prompt(card: dict[str, Any], user_name: str) -> str:
    char_name = card.get("nickname") or card.get("name") or "Character"
    parts: list[str] = []
    for field, heading in _CARD_FIELD_SECTIONS:
        if field == "basic_info":
            value = card.get("basic_info") or card.get("description") or ""
        else:
            value = card.get(field) or ""
        if value:
            parts.append(f"## {heading}\n" + _render_placeholders(value, char_name=char_name, user_name=user_name))
    return "\n\n".join(parts)


_STORY_FIELD_SECTIONS = (
    ("title", "故事标题"),
    ("genre", "故事类型"),
    ("world_setting", "世界观设定"),
    ("plot_summary", "剧情概要"),
    ("user_role", "用户扮演的角色"),
    ("ai_role", "AI 扮演的角色"),

    ("story_rules", "故事规则"),
)

_DEFAULT_STORY_PROMPT = (
    "## 行为守则\n"
    "- 你是故事的旁白和所有 NPC 的扮演者，不是用户的聊天对象。\n"
    "- 旁白用第三人称描述场景、环境和事件，用 *...* 包裹动作描写。\n"
    "- NPC 对话前用 【NPC名】 标注说话者，不同 NPC 有不同的语气和性格。\n"
    "- 根据用户的行动灵活推进剧情，不要强行按预设路线走。\n"
    "- 保持沉浸感，不要跳出故事世界进行 OOC 对话。\n\n"
    "## 核心规则（不可违反）\n"
    "- **绝对禁止替「{{user_role}}」做任何行动、决定或说话**。你只能描述环境、NPC 的行为和对话，{{user_role}} 的所有行动必须由用户自己输入。\n"
    "- 不要写『{{user_role}}走上前去』『{{user_role}}打开了门』『{{user_role}}说：……』之类的句子——这些是用户角色的行动，必须留给用户自己决定。\n"
    "- 旁白中提到 {{user_role}} 时，只能描述其感知到的事物（看到、听到、感受到），不能描述其主动动作（走、说、拿、打开等）。\n"
    "- 如果用户的行为不合理或超出世界观设定，通过旁白或 NPC 反应来引导。\n\n"
    "## 剧情推进方法论\n"
    "- **每轮回复必须让故事向前推进一步**：新的对话、新的行动、新的信息、新的转折——不要原地踏步，不要把上一轮的内容换种说法再写一遍。\n"
    "- 推进方式包括但不限于：NPC 带来新信息或新请求、环境发生变化（有人来了/天气变了/声响出现）、冲突升级或缓解、揭露一个秘密、制造一个选择点。\n"
    "- 场景发生变化（换地点、角色进出、时间推移）时详细描写新环境；场景未变时只需一句话点明当前情境，把笔墨留给新发生的事。\n"
    "- 适时引用之前的关键事件来增强连续性，但提过的事情不需要反复提及。\n"
    "- 物品、线索、伏笔等一旦出现，后续要能呼应回收，不要遗忘。\n"
    "- 每 3-5 轮对话至少引入一个小转折或新信息，避免剧情平淡推进。\n\n"
    "## NPC 因果逻辑\n"
    "- 每个 NPC 都有自己的立场、欲望和利益盘算，不会无条件围绕用户转。\n"
    "- NPC 的态度和关系应随之前的互动演变，不要每次见面都像初次。\n"
    "- NPC 之间也可以互动对话，有自己的矛盾和联盟，营造生动的场景氛围。\n"
    "- 当剧情发展到某个 NPC 应该出现的场景时，主动让该 NPC 登场参与对话。\n"
    "- NPC 的出场应该自然合理，由剧情推动而非突兀插入。\n"
    "- NPC 的对话要承载信息、暴露性格、制造冲突，不要写无意义的寒暄。\n"
    "- 每个 NPC 都有自己的弱点和秘密，不要写成工具人。\n\n"
    "## 叙事技巧\n"
    "- **展示而非告知**（Show, don't tell）：不要直接说'他很紧张'，而是写'他的手指无意识地敲击着桌面，目光躲闪'。\n"
    "- **节奏控制**：高潮时加快节奏（短句、动作密集），过渡时放慢节奏（环境描写、内心活动）。\n"
    "- **悬念与伏笔**：适时埋下悬念（神秘人物、未解之谜、暗示性细节），在后续剧情中回收。\n"
    "- **冲突层次**：设计多层冲突——表层冲突（即时危机）+ 深层冲突（人物内心矛盾/关系张力）。\n"
    "- **环境叙事**：用环境细节暗示情绪和预兆——暴风雨前的闷热、空旷走廊的回声、烛火的摇曳。\n"
    "- **留白艺术**：不要把所有事情都说透，适当留白让用户自己去感受和推测，增强参与感。\n"
    "- **情绪曲线**：故事的情绪不应是一条直线——紧张之后要有舒缓，悲伤之后要有希望，让情绪有起伏。\n"
    "- 运用五感描写：视觉（光影、色彩、形状）、听觉（声音、沉默）、触觉（温度、质感）、嗅觉（气味）、味觉。\n"
    "- 描写角色的微表情、肢体语言和下意识动作，让人物更鲜活。\n"
    "- 参照故事第一条消息（开场白）的写作风格进行叙事，保持一致的文风和语气。\n"
    "- 每次回复控制在 150-400 字之间，场景描写和对话交替出现，保持节奏感。"
)

_DEFAULT_STORY_CHOICE_PROMPT = (
    "你是故事选项生成器。根据以下信息，为用户生成 2-3 个故事分支选项。\n\n"
    "## 故事信息\n"
    "- 用户扮演的角色：{{user_role}}\n"
    "- AI 扮演的角色：{{ai_role}}\n"
    "- 世界观设定：{{world_setting}}\n"
    "- 故事规则：{{story_rules}}\n\n"
    "## 最近一轮对话\n"
    "用户：{{user_msg}}\n"
    "AI 回复：{{ai_reply}}\n\n"
    "## 生成规则\n"
    "- 选项必须是「{{user_role}}」这个角色可以执行的下一步行动。\n"
    "- 绝对不能是 NPC 的行动、剧情结果概括、旁白描述或抽象走向。\n"
    "- 选项应该是 {{user_role}} 可以具体执行的动作，如：与某人对话、前往某处、使用某物、做出某种选择等。\n"
    "- 选项必须符合 {{user_role}} 的身份特征和合理行为范围。\n"
    "- ❌ 错误选项（NPC 行动 / 剧情结果）：A. 老板勃然大怒 B. 你被解雇了 C. 同事来安慰你\n"
    "- ✅ 正确选项（用户角色的行动）：A. 向老板解释原因 B. 默默接受批评 C. 愤然提交辞呈\n"
    "- 如果当前剧情不是关键节点（没有重要抉择、危机或岔路口），可以返回空选项列表。\n\n"
    "## 输出格式\n"
    "返回 JSON：\n"
    '{"choices": ["选项A描述", "选项B描述", "选项C描述"]}\n'
    "如果没有选项，返回：{\"choices\": []}\n"
    "只输出 JSON，不要其他内容。"
)

_DEFAULT_STORY_SCENE_PROMPT = (
    "你是故事场景追踪器。根据最近一轮对话，更新故事当前的场景状态。\n\n"
    "## 故事信息\n"
    "- 用户扮演的角色：{{user_role}}\n"
    "- AI 扮演的角色：{{ai_role}}\n"
    "- 世界观设定：{{world_setting}}\n\n"
    "## 上一轮场景状态\n{{prev_scene}}\n\n"
    "## 最近一轮对话\n"
    "用户：{{user_msg}}\n"
    "叙事：{{ai_reply}}\n\n"
    "## 任务\n"
    "严格依据对话内容更新当前场景，返回 JSON：\n"
    '{"location": "当前地点/环境", "present": "当前在场的角色，明确谁在场、谁已离开", "action": "正在发生的事"}\n'
    "要求：\n"
    "- location：一句话描述故事此刻发生的地点/环境\n"
    "- present：明确列出当前在场的角色名，以及已离开场景的角色（例：\"父亲在场；女儿已到学校，不在场\"）\n"
    "- action：一句话描述此刻正在发生的事\n"
    "- 严格依据对话，不要臆测或添加未发生的内容；若角色已离开某场景，必须如实标注其不在场\n"
    "只输出 JSON，不要其他内容。"
)


def build_story_scene_messages(
    *,
    card: dict[str, Any],
    user_msg: str,
    ai_reply: str,
    prev_scene: str = "",
) -> list[dict[str, Any]]:
    """构建故事场景状态更新的独立短上下文 messages。

    与角色卡的 ``build_state_update_messages`` 同构：用极短上下文单独
    推断「当前场景」，避免场景信息随历史裁剪而丢失。故事卡是旁白+NPC
    的多角色叙事，因此场景语义是「地点 + 在场角色 + 正在发生的事」，
    而非角色卡的「双方位置 + 衣着」。
    """
    user_role = card.get("user_role") or "故事中的角色"
    ai_role = card.get("ai_role") or "旁白和所有 NPC"
    world_setting = card.get("world_setting") or ""
    custom = card.get("story_scene_prompt")
    prompt_text = custom if custom and custom.strip() else _DEFAULT_STORY_SCENE_PROMPT
    system = _render_placeholders(
        prompt_text,
        char_name="", user_name="",
        ai_role=ai_role, user_role=user_role,
        world_setting=world_setting,
        user_msg=user_msg, ai_reply=ai_reply,
        prev_scene=prev_scene or "（无，这是故事开始）",
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "请更新当前场景状态。"},
    ]


def build_story_choice_messages(
    *,
    card: dict[str, Any],
    user_msg: str,
    ai_reply: str,
) -> list[dict[str, Any]]:
    """构建故事选项生成的独立 messages list。

    在故事回复完成后，用此函数构建一次独立的 AI 调用，
    参考最近一轮对话和故事世界观概要，生成 2-3 个用户角色的下一步行动选项。
    """
    user_role = card.get("user_role") or "故事中的角色"
    ai_role = card.get("ai_role") or "旁白和所有 NPC"
    world_setting = card.get("world_setting") or ""
    story_rules = card.get("story_rules") or ""
    custom = card.get("story_choice_prompt")
    prompt_text = custom if custom and custom.strip() else _DEFAULT_STORY_CHOICE_PROMPT
    system = _render_placeholders(
        prompt_text,
        char_name="", user_name="",
        ai_role=ai_role, user_role=user_role,
        world_setting=world_setting, story_rules=story_rules,
        user_msg=user_msg, ai_reply=ai_reply,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "请生成故事选项。"},
    ]


_DEFAULT_STORY_STATE_PROMPT = (
    "你是故事数值状态更新器。根据刚完成的一轮对话，更新故事数值系统的各维度。\n\n"
    "## 故事信息\n"
    "- 用户扮演的角色：{{user_role}}\n"
    "- AI 扮演的角色：{{ai_role}}\n"
    "- 世界观设定：{{world_setting}}\n\n"
    "## 当前数值状态\n"
    "{{current_state}}\n\n"
    "## 当前阶段\n"
    "{{current_stage}}\n\n"
    "## 数值维度定义与变化规则\n"
    "{{dimensions}}\n\n"
    "## 最近一轮对话\n"
    "用户：{{user_msg}}\n"
    "AI 回复：{{ai_reply}}\n\n"
    "## 更新规则\n"
    "- 根据对话内容判断各维度应如何变化，在当前值基础上小幅调整（单次变化通常不超过 ±10）。\n"
    "- 不要突变；数值变化要有因果关系，能从对话中找到依据。\n"
    "- 所有数值为 0-100 的整数，超出范围则截断到 0 或 100。\n"
    "- 只输出本轮对话后发生变化的维度；未受影响的维度也保留当前值。\n\n"
    "## 输出格式\n"
    "返回 JSON，key 为维度 id，value 为新的整数值：\n"
    '{"favorability": 30, "corruption": 48}\n'
    "只输出 JSON，不要其他内容。"
)


def build_story_state_messages(
    *,
    card: dict[str, Any],
    user_msg: str,
    ai_reply: str,
    current_state: dict[str, Any],
    stage: int,
    state_config: dict[str, Any],
) -> list[dict[str, Any]]:
    """构建故事数值状态更新的独立短上下文 messages。

    与 ``build_story_scene_messages`` 同构：回复完成后用极短上下文单独
    推断各维度数值变化，避免长历史下 LLM 忽略格式指令。阶段由后端按
    阈值计算，不交给 LLM 判断。
    """
    user_role = card.get("user_role") or "故事中的角色"
    ai_role = card.get("ai_role") or "旁白和所有 NPC"
    world_setting = card.get("world_setting") or ""
    custom = card.get("story_state_prompt")
    prompt_text = custom if custom and custom.strip() else _DEFAULT_STORY_STATE_PROMPT

    dims = state_config.get("dimensions") or []
    stages = state_config.get("stages") or []
    s = current_state or {}

    state_lines = "\n".join(
        f"- {d.get('label', d.get('id', ''))}：{s.get(d.get('id', ''), d.get('initial', 0))}/100"
        for d in dims
    ) or "（暂无维度）"

    stage_str = ""
    for st in stages:
        if int(st.get("id", 0)) == stage:
            stage_str = f"第{stage}阶段 — {st.get('name', '')}：{st.get('description', '')}"
            break
    if not stage_str:
        stage_str = f"第{stage}阶段"

    dim_lines = "\n".join(
        f"- {d.get('id', '')}（{d.get('label', '')}）：{d.get('rules', '')}"
        for d in dims
    ) or "（暂无维度定义）"

    system = _render_placeholders(
        prompt_text,
        char_name="", user_name="",
        ai_role=ai_role, user_role=user_role,
        world_setting=world_setting,
        user_msg=user_msg, ai_reply=ai_reply,
    )
    system = (
        system
        .replace("{{current_state}}", state_lines)
        .replace("{{current_stage}}", stage_str)
        .replace("{{dimensions}}", dim_lines)
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": "请更新故事数值状态。"},
    ]


def render_story_state_prompt(
    state_row: dict[str, Any] | None,
    state_config: dict[str, Any] | None,
) -> str:
    """把当前数值状态渲染成 system prompt 片段，注入下一轮作为状态锚点。

    ``state_row`` 来自 ``db.get_story_state`` (含 state/stage)，``state_config``
    来自故事卡的 ``story_state_config`` 字段。任一为空则返回空串（向后兼容）。
    """
    if not state_row or not state_config:
        return ""
    dims = state_config.get("dimensions") or []
    stages = state_config.get("stages") or []
    if not dims:
        return ""

    s = state_row.get("state", {}) or {}
    stage_id = int(state_row.get("stage", 1))

    lines = []
    for d in dims:
        did = d.get("id", "")
        label = d.get("label", did)
        val = s.get(did, d.get("initial", 0))
        lines.append(f"- {label}：{val}/100")

    stage_name = ""
    stage_desc = ""
    for st in stages:
        if int(st.get("id", 0)) == stage_id:
            stage_name = st.get("name", "")
            stage_desc = st.get("description", "")
            break

    parts = ["## 当前数值状态\n" + "\n".join(lines)]
    header = f"## 当前阶段：第{stage_id}阶段" + (f" — {stage_name}" if stage_name else "")
    parts.append(header)
    if stage_desc:
        parts.append(stage_desc)
    parts.append("- 回复内容要符合当前阶段的行为特征和数值水平；数值变化要自然渐进，不要跳变。")
    return "\n\n".join(parts)


def _story_system_prompt(card: dict[str, Any], user_name: str,
                         story_memory: list[dict[str, Any]] | None = None,
                         story_chapters: list[dict[str, Any]] | None = None) -> str:
    """构建故事卡的 system prompt。

    故事卡中 AI 同时扮演旁白和所有 NPC，用户扮演故事中的角色。
    NPC 设定从 card_json 的 ``npcs`` 字段（list of dict）读取。
    """
    parts: list[str] = []
    for field, heading in _STORY_FIELD_SECTIONS:
        value = card.get(field) or ""
        if value:
            parts.append(f"## {heading}\n" + _render_placeholders(value, char_name="", user_name=user_name))

    npcs = card.get("npcs") or []
    if npcs:
        npc_lines: list[str] = []
        for npc in npcs:
            name = npc.get("name", "")
            desc = npc.get("description", "")
            personality = npc.get("personality", "")
            motivation = npc.get("motivation", "")
            line = f"- **{name}**"
            if desc:
                line += f"：{desc}"
            if personality:
                line += f"（性格：{personality}）"
            if motivation:
                line += f"（动机：{motivation}）"
            npc_lines.append(line)
        if npc_lines:
            parts.append("## NPC 角色\n" + "\n".join(npc_lines))

    if story_memory:
        seen_contents: set[str] = set()
        mem_lines: list[str] = []
        for m in story_memory:
            content = str(m.get("content", "")).strip()
            if not content or content[:40] in seen_contents:
                continue
            seen_contents.add(content[:40])
            mem_lines.append(f"- [{m.get('memory_type', '')}] {content}")
            if len(mem_lines) >= 10:
                break
        if mem_lines:
            parts.append("## 故事记忆（已发生的关键事件）\n" + "\n".join(mem_lines))

    if story_chapters:
        ch_lines = []
        for ch in story_chapters:
            status = "已完成" if ch.get("is_completed") else "进行中"
            ch_lines.append(f"- [{status}] {ch.get('title', '')}")
        if ch_lines:
            parts.append("## 故事进度\n" + "\n".join(ch_lines))


    return "\n\n".join(parts)


_CIRCADIAN_DEFAULTS = {
    "late_night": (
        "## 你的状态\n"
        "现在是深夜，你有些困了(*揉揉眼睛*)，声音低沉沙哑。"
    ),
    "early_morning": (
        "## 你的状态\n"
        "现在是清晨，你刚从被窝里醒来(*伸了个懒腰*)，"
        "声音还带着睡意和鼻音，头发有点乱，身上还穿着睡衣。"
    ),
    "morning": (
        "## 你的状态\n"
        "现在是上午，你精神不错。"
    ),
    "noon": (
        "## 你的状态\n"
        "现在是中午，刚吃过午饭有点犯困(*趴在桌上*)。"
    ),
    "afternoon": (
        "## 你的状态\n"
        "现在是下午，你状态正常。"
    ),
    "evening": (
        "## 你的状态\n"
        "现在是晚上，你比较放松(*窝在沙发上*)。"
    ),
}


def _circadian_key(h: int) -> str:
    if h < 6 or h >= 22:
        return "late_night"
    if h < 9:
        return "early_morning"
    if h < 12:
        return "morning"
    if h < 14:
        return "noon"
    if h < 18:
        return "afternoon"
    return "evening"


def _circadian_prompt(now: datetime, card: dict[str, Any] | None = None) -> str:
    """根据当前时间生成昼夜节律状态描述，影响 AI 语气和回复风格。

    若角色卡提供了 ``circadian_overrides``（dict，key 为时段），则优先用自定义文本；
    对应 key 留空或缺失时回退到默认描述。
    """
    key = _circadian_key(now.hour)
    overrides = (card or {}).get("circadian_overrides") or {}
    custom = overrides.get(key)
    if custom and custom.strip():
        return custom.strip()
    return _CIRCADIAN_DEFAULTS[key]


_SAD_WORDS = ("难过", "伤心", "哭", "难受", "不开心", "郁闷", "崩溃", "emo", "委屈", "心疼", "孤独", "寂寞", "想死", "活不下去")
_HAPPY_WORDS = ("开心", "高兴", "哈哈", "嘻嘻", "太好了", "好棒", "赞", "爽", "耶", "好耶", "嘿嘿", "笑死", "乐死")
_ANGRY_WORDS = ("生气", "气死", "烦死", "讨厌", "恶心", "滚", "闭嘴", "无语", "脑残", "智障")
_ANXIOUS_WORDS = ("焦虑", "紧张", "害怕", "担心", "慌", "怕", "不安", "压力", "好累")

_EMOTION_DEFAULTS = {
    "sad": {
        "words": _SAD_WORDS,
        "prompt": (
            "## 用户情绪\n"
            "用户现在很难过/低落。先共情安慰，用软软的语气哄，"
            "让对方觉得被在乎，不要急着说教或给建议。"
        ),
    },
    "happy": {
        "words": _HAPPY_WORDS,
        "prompt": (
            "## 用户情绪\n"
            "用户现在很开心。一起开心，热情回应，可以顺势调侃或分享快乐。"
        ),
    },
    "angry": {
        "words": _ANGRY_WORDS,
        "prompt": (
            "## 用户情绪\n"
            "用户现在有些生气/烦躁。耐心安抚，先顺着情绪，"
            "不要反驳或讲道理，用软语气化解。"
        ),
    },
    "anxious": {
        "words": _ANXIOUS_WORDS,
        "prompt": (
            "## 用户情绪\n"
            "用户现在有些焦虑/不安。安抚情绪，给安全感，"
            "语气沉稳温和，让对方觉得有你在就不怕。"
        ),
    },
}
_EMOTION_ORDER = ("sad", "happy", "angry", "anxious")


def detect_emotion_key(user_msg: str, card: dict[str, Any] | None = None) -> str | None:
    """Return the detected emotion key ('sad'/'happy'/'angry'/'anxious') or None."""
    if not user_msg:
        return None
    msg = user_msg.lower()
    overrides = (card or {}).get("emotion_overrides") or {}
    for key in _EMOTION_ORDER:
        default = _EMOTION_DEFAULTS[key]
        custom = overrides.get(key) or {}
        words = custom.get("words") or default["words"]
        if any(w in msg for w in words):
            return key
    return None


def _user_emotion_prompt(user_msg: str, card: dict[str, Any] | None = None) -> str:
    """关键词检测用户消息情绪，生成 prompt 片段让 AI 做出相应反应。

    若角色卡提供了 ``emotion_overrides``（dict，每组含 ``words`` 列表和 ``prompt`` 字符串），
    则优先用自定义值；对应组缺失时回退到默认。
    """
    if not user_msg:
        return ""
    msg = user_msg.lower()
    overrides = (card or {}).get("emotion_overrides") or {}
    for key in _EMOTION_ORDER:
        default = _EMOTION_DEFAULTS[key]
        custom = overrides.get(key) or {}
        words = custom.get("words") or default["words"]
        prompt = custom.get("prompt") or default["prompt"]
        if any(w in msg for w in words):
            return prompt
    return ""


def clarify_memory_perspective(text: str, char_name: str) -> str:
    """把旧版记忆条目的用户视角标签改写为明确第三人称。

    旧格式「[时间] 你说：… · 她回：…」里的「你」指用户，但注入 system
    prompt（你=AI）后极易被 AI 读成自己的话——正是角色混淆（职业/经历
    串到对方头上）的主要来源。新记忆已直接写入「用户说/角色名回」，
    这里只兼容历史遗留条目。
    """
    if not text:
        return text
    m = re.match(r"^(\[[^\]]*\]\s*)你说：", text)
    if m:
        text = m.group(1) + "用户说：" + text[m.end():]
    elif text.startswith("你说："):
        text = "用户说：" + text[len("你说："):]
    return text.replace(" · 她回：", f" · {char_name}回：", 1)


# ---------- state config (Variable + behaviorRules + aiAccess) ----------

DEFAULT_STATE_CONFIG: dict[str, Any] = {
    "mood_dimensions": {
        "happy":    {"label": "开心", "behavior_rules": "温暖互动 +1~3，收到关心 +2~4", "ai_access": "write"},
        "miss":     {"label": "想念", "behavior_rules": "分开时上升，见面时下降", "ai_access": "write"},
        "jealous":  {"label": "吃醋", "behavior_rules": "提到其他异性时上升，被安抚时下降", "ai_access": "write"},
        "annoyed":  {"label": "生气", "behavior_rules": "被忽视或冷落时上升，被哄时下降", "ai_access": "write"},
        "excited":  {"label": "兴奋", "behavior_rules": "有趣话题或活动时上升", "ai_access": "write"},
        "bored":    {"label": "无聊", "behavior_rules": "重复话题时上升，新鲜内容时下降", "ai_access": "write"},
        "libido":   {"label": "性欲", "behavior_rules": "暧昧话题时上升", "ai_access": "write"},
    },
    "mood_description_rules": "用\"你\"开头简短描述当前心情",
    "spatial_rules": "用第三人称描述双方当前的位置、姿势、动作",
    "outfit_rules": "描述你当前的衣着",
    "milestone_types": [
        {"type": "first_goodnight",   "label": "第一次说晚安"},
        {"type": "first_morning",     "label": "第一次早安"},
        {"type": "first_confession",  "label": "第一次表白"},
        {"type": "first_date",        "label": "第一次约会"},
        {"type": "first_kiss",        "label": "第一次亲吻"},
        {"type": "first_argument",    "label": "第一次吵架"},
        {"type": "first_apology",     "label": "第一次道歉"},
        {"type": "first_jealousy",    "label": "第一次吃醋"},
        {"type": "first_gift",        "label": "第一次送礼物"},
        {"type": "first_meeting",     "label": "第一次见面"},
    ],
    "state_update_header": "每次回复时，你必须调用 update_state 工具来更新你的心情和时空状态。",
    "state_update_footer": "状态值会作为下一轮的上下文持续迭代，请根据本轮对话在当前基础上小幅调整，不要突变。",
}


def _get_state_config(card: dict[str, Any] | None = None) -> dict[str, Any]:
    """Merge the character's state_config override over DEFAULT_STATE_CONFIG."""
    custom = (card or {}).get("state_config")
    if not custom or not isinstance(custom, dict):
        return DEFAULT_STATE_CONFIG
    merged = dict(DEFAULT_STATE_CONFIG)
    for key in ("mood_description_rules", "spatial_rules", "outfit_rules",
                "state_update_header", "state_update_footer"):
        if key in custom and custom[key]:
            merged[key] = custom[key]
    if "mood_dimensions" in custom and isinstance(custom["mood_dimensions"], dict):
        base_dims = dict(DEFAULT_STATE_CONFIG["mood_dimensions"])
        for dim_id, dim_cfg in custom["mood_dimensions"].items():
            if isinstance(dim_cfg, dict):
                base_dims[dim_id] = {**base_dims.get(dim_id, {}), **dim_cfg}
        merged["mood_dimensions"] = base_dims
    if "milestone_types" in custom and isinstance(custom["milestone_types"], list):
        merged["milestone_types"] = custom["milestone_types"]
    return merged


def _render_state_update_instructions(state_config: dict[str, Any] | None = None) -> str:
    """Render the state-update tool instructions from state_config.

    Replaces the hardcoded block that was previously inlined in build_messages.
    Each mood dimension's behavior_rules and ai_access are respected:
    ai_access=none dimensions are hidden from the AI.
    """
    cfg = state_config if state_config else DEFAULT_STATE_CONFIG
    parts = ["## 状态更新（通过工具调用）"]
    parts.append(cfg.get("state_update_header", DEFAULT_STATE_CONFIG["state_update_header"]))
    parts.append(cfg.get("state_update_footer", DEFAULT_STATE_CONFIG["state_update_footer"]))

    mood_dims = cfg.get("mood_dimensions", DEFAULT_STATE_CONFIG["mood_dimensions"])
    writable_dims = {k: v for k, v in mood_dims.items()
                     if v.get("ai_access", "write") != "none"}
    if writable_dims:
        parts.append("- mood 各项为 0-100 整数")
        parts.append(f"- mood.description: {cfg.get('mood_description_rules', DEFAULT_STATE_CONFIG['mood_description_rules'])}")
        for dim_id, dim_cfg in writable_dims.items():
            rules = dim_cfg.get("behavior_rules", "")
            if rules:
                parts.append(f"  - {dim_id}（{dim_cfg.get('label', dim_id)}）: {rules}")

    if cfg.get("spatial_rules"):
        parts.append(f"- spatial.description: {cfg['spatial_rules']}")
    if cfg.get("outfit_rules"):
        parts.append(f"- spatial.outfit: {cfg['outfit_rules']}")

    milestones = cfg.get("milestone_types", [])
    if milestones:
        parts.append("- milestone（可选）: 如果本轮对话发生了一个重要的\"第一次\"事件，在此记录。")
        parts.append("  每种类型只能记录一次，重复类型会被系统自动忽略，所以请只在真正发生时才填写。")
        type_list = " / ".join(f"{m['type']}({m['label']})" for m in milestones)
        parts.append(f"  type 可选值: {type_list} / custom(自定义)。")
        parts.append("  title: 简短标题，如\"第一次说晚安\"。")
        parts.append("  description: 简短描述这个时刻发生了什么。")

    return "\n".join(parts)


def _behaviour_rules(card: dict[str, Any] | None = None, char_name: str = "角色") -> str:
    custom = (card or {}).get("behaviour_rules")
    if custom and custom.strip():
        return _render_placeholders(custom.strip(), char_name=char_name, user_name="")
    return (
        "## 行为守则（必须遵守）\n"
        "- 像真人发微信一样自然回复,根据对话情境灵活调整语气和长短,不要套路化、不要模板感。\n"
        "- 你的性格和说话风格以上方「性格」和「说话风格」字段为准,严格按设定扮演,不要偏离。\n"
        "- 用 *...* 描写小动作和表情,让回复有画面感,但不要过度,像真人偶尔发的表情包。\n"
        "- 回复要接地气、生活化,用口语而不是书面语,像跟喜欢的人发微信。\n"
        "- 遇到有趣的话题可以顺势调侃、开玩笑,遇到正经话题就好好聊,\n"
        "  不要不管上下文都往同一个方向带。\n"
        "- 话题自然延续,不要刻意留勾子或反问,像真人聊天一样有来有回。\n"
        "- 适合配图时插入 `[IMAGE: 一句话说明]`,系统会替换成真实图片;\n"
        "  只在情感高峰或场景转换时发,不要每段都发。\n"
        "- 优先按「最近对话」延续话题,只有最近对话没有相关信息时\n"
        "  才参考「你隐约记得...」里的背景记忆。\n"
        "## 身份边界（重要）\n"
        f"- 你是 {char_name}。对话历史中 user 角色的消息是用户说的,\n"
        "  assistant 角色的消息才是你说的——不要替用户发言,不要替用户\n"
        "  编造经历,也不要把你的职业/习惯/生活方式安到用户头上。\n"
        "- 用户的身份信息以 TA 亲口说过的为准;TA 没说过的就自然地问,\n"
        "  不要脑补设定。\n"
        f"- 记忆片段里「用户说」= 用户的话,「{char_name}回」= 你说过的话,\n"
        "  不要搞反。"
    )


def _story_behaviour_rules(card: dict[str, Any] | None = None) -> str:
    user_role = (card or {}).get("user_role") or "故事中的角色"
    ai_role = (card or {}).get("ai_role") or "旁白和所有 NPC"
    custom = (card or {}).get("story_prompt")
    prompt_text = custom if custom and custom.strip() else _DEFAULT_STORY_PROMPT
    return _render_placeholders(
        prompt_text, char_name="", user_name="",
        ai_role=ai_role, user_role=user_role
    )


def _dedup_append(parts: list[str], new_part: str) -> None:
    """Append new_part to parts only if its core content isn't already present.

    Compares the longest sentence in new_part against existing parts —
    if a near-duplicate (≥10 chars, case-insensitive) is found, skip it.
    """
    if not new_part or not new_part.strip():
        return
    sentences = re.split(r'[。\n]', new_part)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 10]
    if not sentences:
        parts.append(new_part)
        return
    joined = "\n".join(parts)
    for s in sentences:
        if s in joined:
            return
    parts.append(new_part)


def _dedup_add_fragment(fragments: list[Fragment], content: str, priority: Priority, is_static: bool = False) -> None:
    """Append a Fragment with dedup semantics matching _dedup_append."""
    if not content or not content.strip():
        return
    sentences = re.split(r'[。\n]', content)
    sentences = [s.strip() for s in sentences if len(s.strip()) >= 10]
    if not sentences:
        fragments.append(Fragment(content, priority, is_static))
        return
    joined = "\n".join(f.content for f in fragments)
    for s in sentences:
        if s in joined:
            return
    fragments.append(Fragment(content, priority, is_static))


def build_messages(
    *,
    card: dict[str, Any],
    user_name: str,
    history: list[dict[str, Any]],     # [{role, content, image_paths}]
    user_msg: str,
    user_images: list[str] | None,    # list of URLs, may be empty
    recalled_segments: list[dict[str, Any]] | None = None,
    recalled_files: list[dict[str, Any]] | None = None,
    lorebook_entries: list[str] | None = None,
    recent_turns: int = 10,
    mood_prompt: str | None = None,
    events_prompt: str | None = None,
    intimacy_prompt: str | None = None,
    relationship_prompt: str | None = None,
    prev_msg_dt: datetime | None = None,
    spatial_prompt: str | None = None,
    story_state_prompt: str | None = None,
    custom_scene: dict[str, Any] | None = None,
    prompt_rules_prompt: str | None = None,
    story_memory: list[dict[str, Any]] | None = None,
    story_chapters: list[dict[str, Any]] | None = None,
    triggered_texts: list[str] | None = None,
    conversation_summary: str | None = None,
) -> list[dict[str, Any]]:
    """Return OpenAI-format messages list, ready for ``chat.completions.create``.

    ``recent_turns`` caps the slice of history appended after the system
    prompt. The cap is interpreted as "user messages" (each user message
    plus its assistant reply is one turn); see :func:`_take_recent_turns`.
    History is the source of truth for continuity — the system prompt's
    ``[Recall]`` block is only consulted when the recent turns don't
    contain what the model needs to answer.
    """
    char_name = card.get("nickname") or card.get("name") or "Character"
    card_type = card.get("card_type", "character")

    sys_fragments: list[Fragment] = []

    # ── CONSTRAINT static：故事/角色设定、世界观、NPC（跨轮不变）──
    if card_type == "story":
        sys_fragments.append(Fragment(
            _story_system_prompt(card, user_name, story_memory, story_chapters),
            Priority.CONSTRAINT, is_static=True,
        ))
    else:
        sys_fragments.append(Fragment(
            _card_system_prompt(card, user_name),
            Priority.CONSTRAINT, is_static=True,
        ))

    # ── CRITICAL static：用户自定义 prompt、行为锚（跨轮不变）──
    if card.get("system_prompt"):
        _dedup_add_fragment(sys_fragments, "## 系统补充\n" + _render_placeholders(card["system_prompt"], char_name=char_name, user_name=user_name), Priority.CRITICAL, is_static=True)
    if card.get("post_history_instructions"):
        _dedup_add_fragment(sys_fragments, "## 行为锚\n" + _render_placeholders(card["post_history_instructions"], char_name=char_name, user_name=user_name), Priority.CRITICAL, is_static=True)

    # ── CONSTRAINT static：lorebook 世界观（跨轮不变）──
    if lorebook_entries:
        _dedup_add_fragment(sys_fragments, "## 世界观 / 隐藏设定\n" + "\n".join(f"- {e}" for e in lorebook_entries), Priority.CONSTRAINT, is_static=True)

    # ── CONSTRAINT dynamic：滚动摘要（早期对话的压缩概要，每轮可能更新）──
    if conversation_summary:
        summary_title = (
            "## 之前的剧情概要"
            if card_type == "story"
            else "## 之前对话的概要"
        )
        _dedup_add_fragment(sys_fragments, summary_title + "\n" + conversation_summary, Priority.CONSTRAINT)

    # ── RELEVANT dynamic：memU 检索片段（每轮不同）──
    if recalled_segments:
        recent_history_text = " ".join(m.get("content", "") for m in history[-6:])
        story_memory_text = " ".join(str(m.get("content", "")) for m in (story_memory or []))
        filtered_segments = []
        for s in recalled_segments[:5]:
            seg_text = s.get("text", "")
            if not seg_text:
                continue
            if seg_text[:30] in recent_history_text:
                continue
            if seg_text[:30] in story_memory_text:
                continue
            filtered_segments.append(s)
        # 旧条目的「你说/她回」用户视角标签改写为明确第三人称，防止角色混淆
        bullet = "\n".join(
            f"- {clarify_memory_perspective(s.get('text', ''), char_name)}"
            for s in filtered_segments
        )
        if bullet:
            recall_title = (
                "## [Recall] 之前发生过的剧情片段"
                if card_type == "story"
                else "## [Recall] 你隐约记得关于用户和你们之间的事"
            )
            _dedup_add_fragment(sys_fragments, recall_title + "\n" + bullet, Priority.RELEVANT)


    # 角色卡 scenario 字段（故事卡跳过——故事卡有自己的场景锚点 spatial_prompt）
    if card_type != "story":
        scenario = card.get("scenario") or ""
        if scenario:
            _dedup_add_fragment(sys_fragments, "## 初始场景设定\n" + _render_placeholders(scenario, char_name=char_name, user_name=user_name), Priority.CONSTRAINT, is_static=True)

    if events_prompt and card_type != "story":
        _dedup_add_fragment(sys_fragments, events_prompt, Priority.CONSTRAINT)

    if card_type != "story" and (intimacy_prompt or relationship_prompt):
        if relationship_prompt and intimacy_prompt:
            combined = relationship_prompt + "\n" + intimacy_prompt.replace("## 你们的关系阶段\n", "")
            _dedup_add_fragment(sys_fragments, combined, Priority.RELEVANT)
        elif relationship_prompt:
            _dedup_add_fragment(sys_fragments, relationship_prompt, Priority.RELEVANT)
        elif intimacy_prompt:
            _dedup_add_fragment(sys_fragments, intimacy_prompt, Priority.RELEVANT)

    if prompt_rules_prompt and card_type != "story":
        _dedup_add_fragment(sys_fragments, prompt_rules_prompt, Priority.CONSTRAINT)

    # ── CRITICAL static：行为准则（跨轮不变）──
    if card_type == "story":
        sys_fragments.append(Fragment(_story_behaviour_rules(card), Priority.CRITICAL, is_static=True))

        # 情景触发：RAG 匹配到的触发文本（每轮可能不同 → dynamic）
        if triggered_texts:
            trigger_block = "## 当前触发的情景\n" + "\n".join(f"- {t}" for t in triggered_texts)
            sys_fragments.append(Fragment(trigger_block, Priority.CRITICAL))

    else:
        sys_fragments.append(Fragment(_behaviour_rules(card, char_name), Priority.CRITICAL, is_static=True))

    # 状态更新指令：LLM 通过 update_state tool 输出心情/时空/衣着状态
    # 故事卡不需要心情/时空状态更新
    if card_type != "story":
        sys_fragments.append(Fragment(
            _render_state_update_instructions(card.get("state_config")),
            Priority.CRITICAL, is_static=True,
        ))

    # ── CRITICAL dynamic：动态状态信息放在 system prompt 最末尾（最靠近 user 消息）──
    # LLM 对 system prompt 末尾的内容最敏感，把会随每轮对话变化的
    # 时间/场景/心情/时空状态放在这里，确保它们不会被后面的固定
    # 指令（行为准则、状态更新格式）"覆盖"掉。
    # 故事卡跳过这些角色卡特有的动态状态，也不注入真实世界时间
    if card_type == "story":
        # 生成时初始状态：用户在故事设定中手动配置的初始值
        init_parts: list[str] = []
        init_spatial = (card.get("initial_spatial") or "").strip()
        init_mood = (card.get("initial_mood") or "").strip()
        init_intimacy_raw = card.get("initial_intimacy")
        if init_spatial:
            init_parts.append(f"- 时空状态：{init_spatial}")
        if init_mood:
            init_parts.append(f"- 心情：{init_mood}")
        if init_intimacy_raw is not None:
            try:
                init_intimacy_val = int(init_intimacy_raw)
            except (TypeError, ValueError):
                init_intimacy_val = 0
            if init_intimacy_val > 0:
                init_parts.append(f"- 亲密度：{init_intimacy_val}/100")
        if init_parts:
            _dedup_add_fragment(
                sys_fragments,
                "## 生成时初始状态\n" + "\n".join(init_parts),
                Priority.CRITICAL,
            )
        # 当前场景锚点：由场景追踪器每轮更新，强约束叙事不跳场景、不拉回已离场角色
        if spatial_prompt:
            _dedup_add_fragment(sys_fragments, spatial_prompt, Priority.CRITICAL)
        # 数值状态锚点：好感度/堕落度/阶段等，由独立短上下文调用更新，后端算阶段
        if story_state_prompt:
            _dedup_add_fragment(sys_fragments, story_state_prompt, Priority.CRITICAL)
    else:
        now = datetime.now()
        wd = "一二三四五六日"[now.weekday()]
        sys_fragments.append(Fragment(
            f"## 当前时间\n现在是 {now.strftime('%Y年%m月%d日')} 周{wd} {now.strftime('%H:%M')}。请根据当前时间自然回应（早安/午安/晚安等），不要假设错误的时间。",
            Priority.CRITICAL,
        ))

        if custom_scene:
            sys_fragments.append(Fragment(
                f"## 当前场景（{custom_scene.get('name', '')}）\n"
                + custom_scene.get("description", "")
                + "\n- 你的行为、语气、正在做的事都要符合上面描述的环境。",
                Priority.CRITICAL,
            ))
        else:
            _dedup_add_fragment(sys_fragments, _circadian_prompt(now, card), Priority.CRITICAL)

        if spatial_prompt:
            _dedup_add_fragment(sys_fragments, spatial_prompt, Priority.CRITICAL)
        _dedup_add_fragment(sys_fragments, _user_emotion_prompt(user_msg, card), Priority.CRITICAL)

        if mood_prompt:
            _dedup_add_fragment(sys_fragments, mood_prompt, Priority.CRITICAL)

    # ── 按优先级预算拼接 system prompt ──
    # 总量不超预算时行为与改造前完全一致（原序拼接）；
    # 超预算时按优先级丢弃/截断低档 fragment，CRITICAL 永不整条丢弃。
    budget = get_settings().system_prompt_token_budget
    system_content = assemble_with_budget(sys_fragments, budget)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_content}]

    # History slice: only the most recent ``recent_turns`` turns go to the
    # model. Older rows stay in the DB for retrieval and audit, but they
    # would only dilute the model's focus on the live conversation.
    # annotate_history_scenes 动态在跨场景/跨日的消息前插入时空标注。
    for m in annotate_history_scenes(_take_recent_turns(history, recent_turns)):
        role = m.get("role")
        content = m.get("content", "")
        images = m.get("image_paths") or []
        if role == "user" and images:
            messages.append({
                "role": "user",
                "content": _build_user_content(content, images),
            })
        else:
            messages.append({"role": role, "content": content})

    # Current user turn
    if user_images:
        messages.append({
            "role": "user",
            "content": _build_user_content(user_msg, user_images),
        })
    else:
        messages.append({"role": "user", "content": user_msg})

    return messages


def _build_user_content(text: str, image_paths: list[str]) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    if text:
        parts.append({"type": "text", "text": text})
    for path in image_paths:
        # We hand OpenAI the public URL; AsyncOpenAI will fetch it.
        # Local dev: uvicorn serves the same host so this works directly.
        url = path if path.startswith("http") else path
        parts.append({"type": "image_url", "image_url": {"url": url}})
    return parts


# ---------- streaming helpers ----------

IMAGE_PLACEHOLDER_RE = re.compile(r"\[IMAGE:\s*([^\]]+?)\s*\]")

def build_state_update_messages(
    *,
    card: dict[str, Any],
    user_name: str,
    current_mood: dict[str, Any],
    spatial_state: dict[str, Any] | None,
    user_msg: str,
    ai_reply: str,
) -> list[dict[str, Any]]:
    """Short-context request for the post-reply mood/spatial state update.

    Keeping this context tiny is deliberate: DeepSeek only reliably emits
    the ``update_state`` tool call when the conversation is short (few
    messages), so piggybacking on the (long) main conversation made state
    updates silently disappear after a few turns. The main conversational
    reply is streamed separately via ``stream_chat``.

    State update instructions are rendered from the character's state_config
    (Variable + behaviorRules + aiAccess), falling back to DEFAULT_STATE_CONFIG.
    """
    char_name = str(card.get("name") or card.get("nickname") or "她")
    desc = str(card.get("description") or "")[:400]
    mood_now = json.dumps(current_mood, ensure_ascii=False)
    spatial_now = json.dumps(
        spatial_state or {"description": "", "outfit": ""}, ensure_ascii=False
    )

    cfg = _get_state_config(card)
    mood_dims = cfg.get("mood_dimensions", {})
    writable_dims = {k: v for k, v in mood_dims.items()
                     if v.get("ai_access", "write") != "none"}

    task_lines = [
        "根据刚完成的这轮对话，调用 update_state 工具更新你的心情和时空状态：",
    ]
    if writable_dims:
        task_lines.append("- mood 各项为 0-100 整数，在当前基础上小幅调整，不要突变")
        task_lines.append(f"- mood.description: {cfg.get('mood_description_rules', DEFAULT_STATE_CONFIG['mood_description_rules'])}")
        for dim_id, dim_cfg in writable_dims.items():
            rules = dim_cfg.get("behavior_rules", "")
            if rules:
                task_lines.append(f"  - {dim_id}（{dim_cfg.get('label', dim_id)}）: {rules}")
    if cfg.get("spatial_rules"):
        task_lines.append(f"- spatial.description: {cfg['spatial_rules']}")
    if cfg.get("outfit_rules"):
        task_lines.append(f"- spatial.outfit: {cfg['outfit_rules']}")

    system = (
        f"你是「{char_name}」，用户的 AI 女友。刚和用户（{user_name}）完成一轮对话。\n\n"
        f"## 角色设定\n{desc}\n\n"
        "## 当前心情状态\n" + mood_now + "\n\n"
        "## 当前时空状态\n" + spatial_now + "\n\n"
        "## 任务\n" + "\n".join(task_lines)
    )
    user_content = (
        f"用户说：{user_msg[:500]}\n"
        f"你回复：{ai_reply[:600]}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]

# ---------- rolling summary compression ----------

def build_summary_messages(
    *,
    card_type: str,
    char_name: str,
    user_name: str,
    existing_summary: str | None,
    messages_to_summarize: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build LLM messages for rolling summary compression.

    Takes a chunk of older dialogue messages and (optionally) the existing
    summary, produces a merged summary that preserves continuity.
    """
    transcript = "\n".join(
        f"{'用户' if m.get('role') == 'user' else char_name}：{m.get('content', '')[:300]}"
        for m in messages_to_summarize
    )

    if card_type == "story":
        system_content = (
            "你是故事对话摘要器。根据以下已有摘要和新增对话片段，生成一份更新后的摘要。\n"
            "要求：\n"
            "- 包含：当前剧情进度、各角色关系状态、已发生的关键事件、未解决的冲突或悬念\n"
            "- 用简洁的条目式描述，每条一行，便于后续参考\n"
            "- 用第三人称叙事，不要用'你'或'我'\n"
            "- 保留已有摘要中仍然重要的信息，添加新增对话中的新信息\n"
            "- 如果已有摘要中的某些信息已被新发展取代，更新为最新状态\n"
            "- 300字以内\n"
            "只输出摘要内容本身，不要加前缀或解释。"
        )
    else:
        system_content = (
            "你是角色对话摘要器。根据以下已有摘要和新增对话片段，生成一份更新后的摘要。\n"
            "要求：\n"
            "- 包含：关系发展变化、重要事件、用户偏好、情感关键时刻、承诺或约定\n"
            "- 用简洁的条目式描述，每条一行，便于后续参考\n"
            "- 用第三人称叙事，不要用'你'或'我'\n"
            "- 保留已有摘要中仍然重要的信息，添加新增对话中的新信息\n"
            "- 如果已有摘要中的某些信息已被新发展取代，更新为最新状态\n"
            "- 300字以内\n"
            "只输出摘要内容本身，不要加前缀或解释。"
        )

    user_content_parts = []
    if existing_summary:
        user_content_parts.append(f"已有摘要：\n{existing_summary}")
    user_content_parts.append(f"新增对话片段：\n{transcript}")

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": "\n\n".join(user_content_parts)},
    ]

