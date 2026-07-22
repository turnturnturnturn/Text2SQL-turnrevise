package com.example.copilot.repository;

import com.example.copilot.domain.AuditEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;

public interface AuditEventRepository extends JpaRepository<AuditEvent, Long> {
    Optional<AuditEvent> findFirstByRequestIdOrderByCreatedAtDesc(String requestId);
}
