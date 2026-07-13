package com.example.copilot.service;

import com.example.copilot.api.ActionDtos.PreviewRequest;
import com.example.copilot.domain.ActionStatus;
import com.example.copilot.domain.ActionType;
import com.example.copilot.domain.CommerceOrder;
import com.example.copilot.domain.OrderStatus;
import com.example.copilot.domain.PendingAction;
import com.example.copilot.repository.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ActionServiceTest {
    private final PendingActionRepository actions = mock(PendingActionRepository.class);
    private final OrderRepository orders = mock(OrderRepository.class);
    private final AuditService audit = mock(AuditService.class);
    private final ActionService service = new ActionService(
            actions, mock(CustomerRepository.class),
            mock(ProductRepository.class), orders,
            mock(OrderItemRepository.class), audit, 5,
            new ObjectMapper());

    @Test
    void analystCannotPreviewWrites() {
        var error = assertThrows(ResponseStatusException.class, () ->
                service.preview(UUID.randomUUID(), "analyst",
                        new PreviewRequest("CANCEL_ORDER", Map.of("orderId", 1))));
        assertEquals(403, error.getStatusCode().value());
    }

    @Test
    void unknownActionTypeIsRejected() {
        var error = assertThrows(ResponseStatusException.class, () ->
                service.preview(UUID.randomUUID(), "operator",
                        new PreviewRequest("DROP_TABLE", Map.of())));
        assertEquals(400, error.getStatusCode().value());
    }

    @Test
    void approvalCannotBeUsedByAnotherUser() {
        UUID owner = UUID.randomUUID();
        PendingAction action = validPendingAction(owner, "approval-token", Map.of("orderId", 1));
        UUID actionId = UUID.randomUUID();
        when(actions.findByIdForUpdate(actionId)).thenReturn(Optional.of(action));

        var error = assertThrows(ResponseStatusException.class, () ->
                service.confirm(actionId, "approval-token", UUID.randomUUID(), "operator"));
        assertEquals(403, error.getStatusCode().value());
    }

    @Test
    void expiredApprovalIsMarkedExpired() {
        UUID owner = UUID.randomUUID();
        PendingAction action = mock(PendingAction.class);
        UUID actionId = UUID.randomUUID();
        when(actions.findByIdForUpdate(actionId)).thenReturn(Optional.of(action));
        when(action.getUserId()).thenReturn(owner);
        when(action.getStatus()).thenReturn(ActionStatus.PENDING);
        when(action.getExpiresAt()).thenReturn(Instant.now().minusSeconds(1));

        var error = assertThrows(ResponseStatusException.class, () ->
                service.confirm(actionId, "approval-token", owner, "operator"));
        assertEquals(409, error.getStatusCode().value());
        verify(action).expire();
        verify(actions).save(action);
    }

    @Test
    void changedPayloadFailsIntegrityCheck() {
        UUID owner = UUID.randomUUID();
        PendingAction action = validPendingAction(owner, "approval-token", Map.of("orderId", 1));
        UUID actionId = UUID.randomUUID();
        when(actions.findByIdForUpdate(actionId)).thenReturn(Optional.of(action));
        when(action.getPayloadHash()).thenReturn(sha256("{}"));

        var error = assertThrows(ResponseStatusException.class, () ->
                service.confirm(actionId, "approval-token", owner, "operator"));
        assertEquals(409, error.getStatusCode().value());
    }

    @Test
    void approvalTokenCannotBeReplayed() {
        UUID owner = UUID.randomUUID();
        PendingAction action = mock(PendingAction.class);
        UUID actionId = UUID.randomUUID();
        when(actions.findByIdForUpdate(actionId)).thenReturn(Optional.of(action));
        when(action.getUserId()).thenReturn(owner);
        when(action.getStatus()).thenReturn(ActionStatus.CONFIRMED);

        var error = assertThrows(ResponseStatusException.class, () ->
                service.confirm(actionId, "approval-token", owner, "operator"));
        assertEquals(409, error.getStatusCode().value());
    }

    @Test
    void alteredApprovalTokenIsRejected() {
        UUID owner = UUID.randomUUID();
        PendingAction action = validPendingAction(owner, "correct-token", Map.of("orderId", 1));
        UUID actionId = UUID.randomUUID();
        when(actions.findByIdForUpdate(actionId)).thenReturn(Optional.of(action));

        var error = assertThrows(ResponseStatusException.class, () ->
                service.confirm(actionId, "altered-token", owner, "operator"));
        assertEquals(403, error.getStatusCode().value());
    }

    @Test
    void changedOrderVersionCannotBeConfirmed() {
        UUID owner = UUID.randomUUID();
        Map<String, Object> payload = Map.of("orderId", 1);
        PendingAction action = validPendingAction(owner, "approval-token", payload);
        CommerceOrder order = mock(CommerceOrder.class);
        UUID actionId = UUID.randomUUID();
        when(actions.findByIdForUpdate(actionId)).thenReturn(Optional.of(action));
        when(action.getActionType()).thenReturn(ActionType.CANCEL_ORDER);
        when(action.getTargetVersion()).thenReturn(1L);
        when(orders.findByIdAndDeletedAtIsNull(1L)).thenReturn(Optional.of(order));
        when(order.getVersion()).thenReturn(2L);
        when(order.getStatus()).thenReturn(OrderStatus.DRAFT);

        var error = assertThrows(ResponseStatusException.class, () ->
                service.confirm(actionId, "approval-token", owner, "operator"));
        assertEquals(409, error.getStatusCode().value());
    }

    private PendingAction validPendingAction(UUID owner, String token, Map<String, Object> payload) {
        PendingAction action = mock(PendingAction.class);
        when(action.getUserId()).thenReturn(owner);
        when(action.getStatus()).thenReturn(ActionStatus.PENDING);
        when(action.getExpiresAt()).thenReturn(Instant.now().plusSeconds(60));
        when(action.getApprovalTokenHash()).thenReturn(sha256(token));
        when(action.getNormalizedPayload()).thenReturn(payload);
        when(action.getPayloadHash()).thenReturn(sha256("{\"orderId\":1}"));
        return action;
    }

    private static String sha256(String value) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8)));
        } catch (Exception ex) {
            throw new IllegalStateException(ex);
        }
    }
}
