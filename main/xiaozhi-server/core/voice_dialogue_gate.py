import random

from core.providers.tts.dto.dto import ContentType, TTSMessageDTO, SentenceType


SPIRIT_POWER_NOTICES = (
    "我的灵力用完啦，让我休息一会儿，恢复后再陪你聊。",
    "我有点累啦，灵力恢复以后再来找我说话吧。",
    "今天的灵力暂时见底啦，我需要休息一下。",
    "我的灵力不够啦，先让我安静恢复一会儿吧。",
    "我得补充一点灵力啦，休息好就继续陪你。",
    "灵力已经耗尽啦，让我小睡一会儿再陪你玩。",
)


def enqueue_spirit_power_notice(tts, sentence_id: str) -> str:
    """Queue an audible reply after the caller has opened the TTS turn."""
    notice = random.choice(SPIRIT_POWER_NOTICES)
    tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=sentence_id,
            sentence_type=SentenceType.MIDDLE,
            content_type=ContentType.TEXT,
            content_detail=notice,
        )
    )
    tts.tts_text_queue.put(
        TTSMessageDTO(
            sentence_id=sentence_id,
            sentence_type=SentenceType.LAST,
            content_type=ContentType.ACTION,
        )
    )
    return notice
