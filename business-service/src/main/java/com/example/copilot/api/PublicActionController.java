package com.example.copilot.api;

import com.example.copilot.api.ActionDtos.*;
import com.example.copilot.service.ActionService;
import jakarta.validation.Valid;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import java.util.UUID;

@RestController
@RequestMapping("/api/actions")
public class PublicActionController {
    private final ActionService actionService;

    public PublicActionController(ActionService actionService) {
        this.actionService = actionService;
    }

    @PostMapping("/{id}/confirm")
    public ActionResponse confirm(@PathVariable UUID id, Authentication authentication,
                                  @Valid @RequestBody ApprovalRequest request) {
        return actionService.confirm(id, request.approvalToken(), UUID.fromString(authentication.getName()), role(authentication));
    }

    @PostMapping("/{id}/cancel")
    public ActionResponse cancel(@PathVariable UUID id, Authentication authentication,
                                 @Valid @RequestBody ApprovalRequest request) {
        return actionService.cancel(id, request.approvalToken(), UUID.fromString(authentication.getName()), role(authentication));
    }

    private String role(Authentication authentication) {
        return authentication.getAuthorities().stream().findFirst()
                .map(value -> value.getAuthority().replace("ROLE_", ""))
                .orElse("analyst");
    }
}

