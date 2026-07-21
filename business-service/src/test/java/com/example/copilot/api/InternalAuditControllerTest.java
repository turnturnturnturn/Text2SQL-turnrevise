package com.example.copilot.api;

import com.example.copilot.service.AuditService;
import org.junit.jupiter.api.Test;
import org.springframework.web.server.ResponseStatusException;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class InternalAuditControllerTest {
    private final AuditService service = mock(AuditService.class);
    private final InternalAuditController controller =
            new InternalAuditController(service, "service-secret");

    @Test
    void runAuditRequiresServiceToken() {
        var error = assertThrows(ResponseStatusException.class,
                () -> controller.getRunAudit("wrong", "run-1"));
        assertEquals(403, error.getStatusCode().value());
    }

    @Test
    void runAuditReturnsAuthoritySqlForValidService() {
        when(service.getRunAudit("run-1")).thenReturn(Optional.of(
                new AuditService.RunAudit("run-1", "QUERY_EXECUTED", "SELECT 1", true, 12L)));
        var result = controller.getRunAudit("service-secret", "run-1");
        assertEquals("SELECT 1", result.generatedSql());
        assertEquals("run-1", result.runId());
    }
}
