package com.example.copilot.security;

import com.example.copilot.domain.AppUser;
import com.example.copilot.domain.Role;
import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.security.Keys;
import org.junit.jupiter.api.Test;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

class JwtServiceTest {
    private static final String SECRET =
            "test-secret-that-is-long-enough-for-hs256-signing-123456789";

    @Test
    void issuesHs256TokenCompatibleWithAgentService() {
        AppUser user = mock(AppUser.class);
        UUID id = UUID.randomUUID();
        when(user.getId()).thenReturn(id);
        when(user.getUsername()).thenReturn("analyst");
        when(user.getEmail()).thenReturn("analyst@example.com");
        when(user.getRole()).thenReturn(Role.analyst);

        String token = new JwtService(SECRET, 60).issue(user);
        var parsed = Jwts.parser()
                .verifyWith(Keys.hmacShaKeyFor(SECRET.getBytes(StandardCharsets.UTF_8)))
                .build()
                .parseSignedClaims(token);

        assertEquals("HS256", parsed.getHeader().getAlgorithm());
        assertEquals(id.toString(), parsed.getPayload().getSubject());
        assertEquals("analyst", parsed.getPayload().get("role", String.class));
    }
}
