package com.example.copilot.domain;

import jakarta.persistence.*;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;
import java.time.Instant;
import java.util.Map;
import java.util.UUID;

@Entity
@Table(name = "pending_actions")
public class PendingAction {
    @Id
    private UUID id;
    @Column(name = "user_id")
    private UUID userId;
    @Enumerated(EnumType.STRING)
    @Column(name = "action_type")
    private ActionType actionType;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "normalized_payload", columnDefinition = "jsonb")
    private Map<String, Object> normalizedPayload;
    @Column(name = "payload_hash")
    private String payloadHash;
    @Column(name = "target_version")
    private Long targetVersion;
    @JdbcTypeCode(SqlTypes.JSON)
    @Column(name = "impact_summary", columnDefinition = "jsonb")
    private Map<String, Object> impactSummary;
    @Enumerated(EnumType.STRING)
    private ActionStatus status;
    @Column(name = "approval_token_hash")
    private String approvalTokenHash;
    @Column(name = "expires_at")
    private Instant expiresAt;
    @Column(name = "created_at")
    private Instant createdAt;
    @Column(name = "confirmed_at")
    private Instant confirmedAt;
    @Column(name = "failure_reason")
    private String failureReason;

    protected PendingAction() {}

    public PendingAction(UUID userId, ActionType actionType, Map<String, Object> payload,
                         String payloadHash, Long targetVersion, Map<String, Object> impact,
                         String tokenHash, Instant expiresAt) {
        this.id = UUID.randomUUID();
        this.userId = userId;
        this.actionType = actionType;
        this.normalizedPayload = payload;
        this.payloadHash = payloadHash;
        this.targetVersion = targetVersion;
        this.impactSummary = impact;
        this.status = ActionStatus.PENDING;
        this.approvalTokenHash = tokenHash;
        this.expiresAt = expiresAt;
        this.createdAt = Instant.now();
    }

    public void confirm() { this.status = ActionStatus.CONFIRMED; this.confirmedAt = Instant.now(); }
    public void cancel() { this.status = ActionStatus.CANCELLED; }
    public void expire() { this.status = ActionStatus.EXPIRED; }
    public void fail(String reason) { this.status = ActionStatus.FAILED; this.failureReason = reason; }

    public UUID getId() { return id; }
    public UUID getUserId() { return userId; }
    public ActionType getActionType() { return actionType; }
    public Map<String, Object> getNormalizedPayload() { return normalizedPayload; }
    public String getPayloadHash() { return payloadHash; }
    public Long getTargetVersion() { return targetVersion; }
    public Map<String, Object> getImpactSummary() { return impactSummary; }
    public ActionStatus getStatus() { return status; }
    public String getApprovalTokenHash() { return approvalTokenHash; }
    public Instant getExpiresAt() { return expiresAt; }
}
