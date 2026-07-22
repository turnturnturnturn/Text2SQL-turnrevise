package com.example.copilot.security;

import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.cors.CorsConfiguration;

import static org.junit.jupiter.api.Assertions.assertEquals;

class SecurityConfigTest {
    @Test
    void configuredAgentOriginsAreUsedByCorsPolicy() {
        var security = new SecurityConfig(
                "http://localhost:8000,http://127.0.0.1:18000");
        var request = new MockHttpServletRequest("OPTIONS", "/api/auth/login");

        CorsConfiguration cors = security.corsConfigurationSource()
                .getCorsConfiguration(request);

        assertEquals(
                java.util.List.of("http://localhost:8000", "http://127.0.0.1:18000"),
                cors.getAllowedOrigins());
    }
}
