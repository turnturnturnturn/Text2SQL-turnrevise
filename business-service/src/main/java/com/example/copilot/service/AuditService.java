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
import java.util.UUID;

@Service
public class AuditService {
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
}
