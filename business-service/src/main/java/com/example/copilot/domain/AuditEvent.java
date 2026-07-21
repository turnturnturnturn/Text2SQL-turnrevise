package com.example.copilot.domain;

import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "audit_events")
public class AuditEvent {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @Column(name = "user_id")
    private UUID userId;
    @Column(name = "event_type")
    private String eventType;
    @Column(name = "action_id")
    private UUID actionId;
    @Column(name = "request_id")
    private String requestId;
    @Column(name = "generated_sql")
    private String generatedSql;
    @Column(name = "original_instruction_hash")
    private String originalInstructionHash;
    private boolean success;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(columnDefinition = "jsonb")
    private Map<String, Object> details;
    @Column(name = "created_at")
    private Instant createdAt;
    @Column(name = "duration_ms")
    private Long durationMs;

    protected AuditEvent() {}

    public String getRequestId() { return requestId; }
    public String getEventType() { return eventType; }
    public String getGeneratedSql() { return generatedSql; }
    public boolean isSuccess() { return success; }
    public Long getDurationMs() { return durationMs; }

    public AuditEvent(UUID userId, String eventType, UUID actionId, boolean success,
                      Map<String, Object> details) {
        this.userId = userId;
        this.eventType = eventType;
        this.actionId = actionId;
        this.success = success;
        this.details = details;
        this.createdAt = Instant.now();
    }

    public AuditEvent(UUID userId, String eventType, String requestId,
                      String originalInstructionHash, String generatedSql,
                      boolean success, Long durationMs, Map<String, Object> details) {
        this.userId = userId;
        this.eventType = eventType;
        this.requestId = requestId;
        this.originalInstructionHash = originalInstructionHash;
        this.generatedSql = generatedSql;
        this.success = success;
        this.durationMs = durationMs;
        this.details = details;
        this.createdAt = Instant.now();
    }
}
