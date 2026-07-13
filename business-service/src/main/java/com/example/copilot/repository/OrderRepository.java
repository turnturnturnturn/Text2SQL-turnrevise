package com.example.copilot.repository;

import com.example.copilot.domain.CommerceOrder;
import org.springframework.data.jpa.repository.JpaRepository;
import java.util.Optional;

public interface OrderRepository extends JpaRepository<CommerceOrder, Long> {
    Optional<CommerceOrder> findByIdAndDeletedAtIsNull(Long id);
}

