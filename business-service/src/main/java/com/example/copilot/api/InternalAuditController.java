package com.example.copilot.api;

import com.example.copilot.service.AuditService;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@RestController
@RequestMapping("/internal/audit")
public class InternalAuditController {
    public record RunAuditResponse(String runId, String eventType, String generatedSql,
                                   boolean success, Long durationMs) {}
    private final AuditService auditService;
    private final String serviceToken;

    public InternalAuditController(AuditService auditService,
                                   @Value("${copilot.internal-service-token}") String serviceToken) {
        this.auditService = auditService;
        this.serviceToken = serviceToken;
    }

    @PostMapping("/events")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void record(@RequestHeader("X-Internal-Service-Token") String token,
                       @Valid @RequestBody AgentAuditRequest request) {
        if (!MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                serviceToken.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Invalid internal service token");
        }
        auditService.recordAgentEvent(request);
    }

    @GetMapping("/runs/{runId}")
    public RunAuditResponse getRunAudit(
            @RequestHeader("X-Internal-Service-Token") String token,
            @PathVariable String runId) {
        verifyServiceToken(token);
        var audit = auditService.getRunAudit(runId)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "Run audit not found"));
        return new RunAuditResponse(audit.runId(), audit.eventType(), audit.generatedSql(),
                audit.success(), audit.durationMs());
    }

    private void verifyServiceToken(String token) {
        if (!MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                serviceToken.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Invalid internal service token");
        }
    }
}
