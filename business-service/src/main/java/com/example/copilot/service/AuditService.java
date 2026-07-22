package com.example.copilot.service;

import com.example.copilot.api.AgentAuditRequest;
import com.example.copilot.domain.AuditEvent;
import com.example.copilot.repository.AuditEventRepository;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;

@Service
public class AuditService {
    public record RunAudit(String runId, String eventType, String generatedSql,
                           boolean success, Long durationMs) {}
    private final AuditEventRepository events;

    public AuditService(AuditEventRepository events) {
        this.events = events;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void record(UUID userId, String eventType, UUID actionId, boolean success,
                       Map<String, Object> details) {
        events.save(new AuditEvent(userId, eventType, actionId, success, details));
    }

    public void recordAfterCommit(UUID userId, String eventType, UUID actionId, boolean success,
                                  Map<String, Object> details) {
        if (!TransactionSynchronizationManager.isActualTransactionActive()) {
            record(userId, eventType, actionId, success, details);
            return;
        }
        TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
            @Override
            public void afterCommit() {
                record(userId, eventType, actionId, success, details);
            }
        });
    }

    @Transactional
    public void recordAgentEvent(AgentAuditRequest request) {
        events.save(new AuditEvent(
                request.userId(), request.eventType(), request.requestId(),
                request.originalInstructionHash(), request.generatedSql(), request.success(),
                request.durationMs(), request.details()));
    }

    @Transactional(readOnly = true)
    public Optional<RunAudit> getRunAudit(String runId) {
        return events.findFirstByRequestIdOrderByCreatedAtDesc(runId).map(event ->
                new RunAudit(event.getRequestId(), event.getEventType(),
                        event.getGeneratedSql(), event.isSuccess(), event.getDurationMs()));
    }
}
