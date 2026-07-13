package com.example.copilot.api;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import java.util.Map;
import java.util.UUID;

public record AgentAuditRequest(
        @NotBlank String eventType,
        @NotNull UUID userId,
        @NotBlank String requestId,
        String originalInstructionHash,
        String generatedSql,
        boolean success,
        Long durationMs,
        @NotNull Map<String, Object> details) {}
