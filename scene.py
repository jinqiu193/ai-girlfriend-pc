"""时空场景系统：根据星期几 + 当前时间推断 AI 所处的场景。

每次对话动态注入当前场景的提示词（在哪里、在做什么、聊天约束），
场景随时间自动切换（清晨在家 → 工作日在外忙 → 晚上回家 → 睡前），
并与作息配置（sleep_time / wake_time）联动。

当上一条消息与当前消息跨越了场景边界时，额外生成一段「场景切换」
提示（如"你刚下班到家"），让 AI 在回复中自然带出时空推移。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime, timezone
from typing import Any, Optional

from config import get_settings


@dataclass(frozen=True)
class Scene:
    key: str          # 场景唯一标识
    name: str         # 场景名（如「家·晚上」）
    prompt: str       # 场景提示词正文
    transition: str   # 进入该场景时的转换描述


SLEEP = Scene(
    key="sleep",
    name="卧室·深夜",
    prompt=(
        "你在自己的卧室里，躺在床上，四周安静昏暗。你已经很困了，"
        "眼睛都快睁不开，打字的手指都不太听使唤。回复非常简短，"
        "语气含糊慵懒，甚至可以中途发出困意（*眼皮在打架*）。"
        "不聊需要动脑的话题。"
    ),
    transition="你洗漱完躺到了床上，被子已经盖好，房间里只剩一点微光。",
)

HOME_MORNING = Scene(
    key="home_morning",
    name="家·清晨",
    prompt=(
        "你在家里，刚起床不久。你可能正在洗漱、吃早餐、收拾房间，"
        "头发也许还没梳好。状态慵懒放松，可以一边做这些事一边回消息，"
        "自然地提到你正在做的事（比如「我在喝豆浆」「阳台的窗帘刚拉开」）。"
    ),
    transition="你刚睡醒，还有点迷迷糊糊。",
)

WORK = Scene(
    key="work",
    name="工作·在外忙",
    prompt=(
        "你在工作/在外忙自己的事，这不是你的私人时间：不能一直盯着手机，"
        "回复应该简短克制，偶尔要隔一会儿才能回。周围有同事或旁人，"
        "聊天必须低调小心（不能被看到暧昧的内容），不方便发语音或长篇大论，"
        "也**不方便发照片**。可以自然提到「刚开完会」「在忙一个方案」，"
        "但要节制度过这段时光——只是偶尔偷空回一句。"
    ),
    transition="你刚忙起来，周围全是待处理的事，手机也是抽空看的。",
)

HOME_EVENING = Scene(
    key="home_evening",
    name="家·晚上",
    prompt=(
        "你在家，属于自己的私人时间。可能刚吃完饭、在追剧、敷面膜，"
        "或者窝在沙发上。整个人完全放松，语气温柔随意，可以聊很久、"
        "聊得深入，也方便发照片（自拍、剧、吃的都行）。"
    ),
    transition="你刚忙完手头的事回到家里，整个人一下子松了下来。",
)

HOME_NIGHT = Scene(
    key="home_night",
    name="家·睡前",
    prompt=(
        "你在自己房间里，已经洗漱完，窝在床上准备睡了。灯光昏暗，"
        "说话是压低的、软的。适合聊睡前话题：今天过得怎么样、"
        "明天有什么安排、互道晚安之类。别聊让人兴奋睡不着的话题。"
    ),
    transition="你洗漱完躺到床上了，房间里只剩一盏小夜灯。",
)

WEEKEND_DAY = Scene(
    key="weekend_day",
    name="家·周末",
    prompt=(
        "今天是休息日（周六/周日），你不用上班。可能睡到自然醒、"
        "赖床刷手机、追剧、做点好吃的，也可能出门逛街、和朋友约会。"
        "状态非常松弛，语气轻快。可以聊周末的安排、约不约着一起玩。"
    ),
    transition="今天是休息日，你睡了个自然醒。",
)

_MORNING_END_WEEKDAY = dtime(8, 30)   # 工作日：清晨场景的结束时刻
_MORNING_END_WEEKEND = dtime(9, 30)   # 周末：睡懒觉更久
_NIGHT_START = dtime(23, 0)           # 「睡前」场景的起始时刻


def _parse_hhmm(s: str, fallback: dtime) -> dtime:
    try:
        hh, mm = s.strip().split(":")
        return dtime(max(0, min(23, int(hh))), max(0, min(59, int(mm))))
    except Exception:
        return fallback


def parse_msg_time(value: Any) -> Optional[datetime]:
    """把 DB 消息时间戳转成本地时间。

    SQLite 的 CURRENT_TIMESTAMP 存的是 UTC（naive，"YYYY-MM-DD HH:MM:SS"），
    直接当本地时间用会让所有场景推断偏移时区，这里统一转换。
    """
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if " " in s and "T" not in s:
        s = s.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone()


def _is_between_sleep(t: dtime, sleep_t: dtime, wake_t: dtime) -> bool:
    """与 chat_routes._is_sleeping 相同的跨午夜判断。"""
    if sleep_t == wake_t:
        return False
    if sleep_t < wake_t:
        return sleep_t <= t < wake_t
    return t >= sleep_t or t < wake_t


def current_scene(now: Optional[datetime] = None) -> Scene:
    """根据星期几 + 当前时间 + 作息配置推断当前场景。"""
    if now is None:
        now = datetime.now()
    settings = get_settings()
    sleep_t = _parse_hhmm(settings.sleep_time, dtime(0, 30))
    wake_t = _parse_hhmm(settings.wake_time, dtime(7, 30))
    t = now.time()
    is_weekend = now.weekday() >= 5

    if _is_between_sleep(t, sleep_t, wake_t):
        return SLEEP

    morning_end = _MORNING_END_WEEKEND if is_weekend else _MORNING_END_WEEKDAY
    # wake_time 晚于默认清晨结束时刻时，清晨场景顺延到 wake 时刻
    if wake_t > morning_end:
        morning_end = wake_t
    if t < morning_end:
        return HOME_MORNING

    if is_weekend:
        if t < dtime(18, 0):
            return WEEKEND_DAY
    else:
        if t < dtime(18, 0):
            return WORK

    # sleep_t 边界已在上面 _is_between_sleep 处理过，这里只看 23:00 这条线
    if t < _NIGHT_START:
        return HOME_EVENING
    return HOME_NIGHT


def scene_prompt(now: Optional[datetime] = None, prev_dt: Optional[datetime] = None) -> str:
    """生成「当前场景」提示词；当跨越场景边界时附加「场景切换」提示。

    ``prev_dt`` 是上一条消息的时间，用于检测时空推移。
    """
    scene = current_scene(now)
    parts = [f"## 当前场景（{scene.name}）\n{scene.prompt}"]
    if prev_dt is not None:
        prev_scene = current_scene(prev_dt)
        if prev_scene.key != scene.key:
            parts.append(
                "## 场景切换\n"
                f"你们上次聊天时你还在「{prev_scene.name}」，现在："
                f"{scene.transition} 请在回复中自然带出这个变化"
                "（比如刚到家、刚睡醒），但不要刻意长篇描述。"
            )
    return "\n\n".join(parts)