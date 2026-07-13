package com.example.copilot.api;

import com.example.copilot.api.ActionDtos.*;
import com.example.copilot.service.ActionService;
import jakarta.validation.Valid;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

@RestController
@RequestMapping("/internal/actions")
public class InternalActionController {
    private final ActionService actionService;
    private final String serviceToken;

    public InternalActionController(ActionService actionService,
                                    @Value("${copilot.internal-service-token}") String serviceToken) {
        this.actionService = actionService;
        this.serviceToken = serviceToken;
    }

    @PostMapping("/preview")
    public PreviewResponse preview(@RequestHeader("X-Internal-Service-Token") String token,
                                   @RequestHeader("X-User-Id") UUID userId,
                                   @RequestHeader("X-User-Role") String role,
                                   @Valid @RequestBody PreviewRequest request) {
        verifyInternalToken(token);
        return actionService.preview(userId, role, request);
    }

    @PostMapping("/{id}/confirm")
    public ActionResponse confirm(@PathVariable UUID id,
                                  @RequestHeader("X-Internal-Service-Token") String token,
                                  @RequestHeader("X-User-Id") UUID userId,
                                  @RequestHeader("X-User-Role") String role,
                                  @Valid @RequestBody ApprovalRequest request) {
        verifyInternalToken(token);
        return actionService.confirm(id, request.approvalToken(), userId, role);
    }

    @PostMapping("/{id}/cancel")
    public ActionResponse cancel(@PathVariable UUID id,
                                 @RequestHeader("X-Internal-Service-Token") String token,
                                 @RequestHeader("X-User-Id") UUID userId,
                                 @RequestHeader("X-User-Role") String role,
                                 @Valid @RequestBody ApprovalRequest request) {
        verifyInternalToken(token);
        return actionService.cancel(id, request.approvalToken(), userId, role);
    }

    private void verifyInternalToken(String supplied) {
        if (!MessageDigest.isEqual(supplied.getBytes(StandardCharsets.UTF_8),
                serviceToken.getBytes(StandardCharsets.UTF_8))) {
            throw new ResponseStatusException(HttpStatus.FORBIDDEN, "Invalid internal service token");
        }
    }
}

