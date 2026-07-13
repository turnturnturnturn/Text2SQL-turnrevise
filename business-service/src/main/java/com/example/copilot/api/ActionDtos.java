package com.example.copilot.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

public final class ActionDtos {
    private ActionDtos() {}

    public record PreviewRequest(@NotBlank String actionType, @NotNull Map<String, Object> payload) {}
    public record ApprovalRequest(@NotBlank String approvalToken) {}
    public record PreviewResponse(UUID actionId, String approvalToken, Map<String, Object> impactSummary,
                                  Instant expiresAt, String status) {}
    public record ActionResponse(UUID actionId, String status, String message, Map<String, Object> result) {}
}

