from __future__ import annotations

from vanna.core.storage import Conversation, ConversationStore, Message
from vanna.core.user import User

from app.state.repositories import StateRepository


class PostgresConversationStore(ConversationStore):
    """Vanna conversation adapter backed by a user-scoped state repository."""

    def __init__(self, repository: StateRepository) -> None:
        self.repository = repository

    async def create_conversation(
        self, conversation_id: str, user: User, initial_message: str
    ) -> Conversation:
        conversation = Conversation(
            id=conversation_id,
            user=user,
            messages=[Message(role="user", content=initial_message)],
        )
        await self.repository.create_conversation(
            conversation.model_dump(mode="json")
        )
        return conversation

    async def get_conversation(
        self, conversation_id: str, user: User
    ) -> Conversation | None:
        payload = await self.repository.get_conversation(conversation_id, user.id)
        return Conversation.model_validate(payload) if payload else None

    async def update_conversation(self, conversation: Conversation) -> None:
        await self.repository.save_conversation(conversation.model_dump(mode="json"))

    async def delete_conversation(self, conversation_id: str, user: User) -> bool:
        return await self.repository.delete_conversation(conversation_id, user.id)

    async def list_conversations(
        self, user: User, limit: int = 50, offset: int = 0
    ) -> list[Conversation]:
        payloads = await self.repository.list_conversations(
            user.id, max(0, limit), max(0, offset)
        )
        return [Conversation.model_validate(payload) for payload in payloads]
