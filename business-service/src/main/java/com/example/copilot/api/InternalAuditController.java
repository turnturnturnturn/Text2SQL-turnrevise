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
}
