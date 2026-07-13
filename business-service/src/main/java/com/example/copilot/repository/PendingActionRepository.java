package com.example.copilot.repository;

import com.example.copilot.domain.PendingAction;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.Optional;
import java.util.UUID;

public interface PendingActionRepository extends JpaRepository<PendingAction, UUID> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select action from PendingAction action where action.id = :id")
    Optional<PendingAction> findByIdForUpdate(@Param("id") UUID id);
}

