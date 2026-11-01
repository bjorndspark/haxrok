"""Chat persistence. Functions flush but never commit — the service owns the
transaction boundary so multi-write request flows commit once."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.features.chat.models import Conversation, Message


async def create_conversation(db: AsyncSession, title: str = "") -> Conversation:
    conv = Conversation(title=title)
    db.add(conv)
    await db.flush()
    return conv


async def list_conversations(db: AsyncSession) -> list[Conversation]:
    result = await db.execute(select(Conversation).order_by(Conversation.id.desc()))
    return list(result.scalars())


async def get_conversation(db: AsyncSession, conversation_id: int) -> Conversation | None:
    return await db.get(Conversation, conversation_id)


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    await db.delete(conv)
    await db.flush()


async def add_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    media_url: str | None = None,
) -> Message:
    msg = Message(conversation_id=conversation_id, role=role, content=content, media_url=media_url)
    db.add(msg)
    await db.flush()
    return msg


async def list_messages(db: AsyncSession, conversation_id: int) -> list[Message]:
    result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)
    )
    return list(result.scalars())
