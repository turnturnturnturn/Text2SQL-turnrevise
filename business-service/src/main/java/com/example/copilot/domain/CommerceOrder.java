package com.example.copilot.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "orders")
public class CommerceOrder {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "customer_id")
    private Customer customer;
    @Enumerated(EnumType.STRING)
    private OrderStatus status;
    @Column(name = "total_amount")
    private BigDecimal totalAmount;
    private String region;
    @Column(name = "paid_at")
    private Instant paidAt;
    @Column(name = "created_at")
    private Instant createdAt;
    @Column(name = "updated_at")
    private Instant updatedAt;
    @Version
    private Long version;
    @Column(name = "deleted_at")
    private Instant deletedAt;

    protected CommerceOrder() {}

    public static CommerceOrder draft(Customer customer, BigDecimal total) {
        CommerceOrder order = new CommerceOrder();
        order.customer = customer;
        order.status = OrderStatus.DRAFT;
        order.totalAmount = total;
        order.region = customer.getRegion();
        order.createdAt = Instant.now();
        order.updatedAt = order.createdAt;
        order.version = 0L;
        return order;
    }

    public void changeStatus(OrderStatus target) {
        this.status = target;
        this.updatedAt = Instant.now();
        if (target == OrderStatus.PAID && paidAt == null) paidAt = Instant.now();
    }

    public void softDelete() {
        this.deletedAt = Instant.now();
        this.updatedAt = this.deletedAt;
    }

    public Long getId() { return id; }
    public Customer getCustomer() { return customer; }
    public OrderStatus getStatus() { return status; }
    public BigDecimal getTotalAmount() { return totalAmount; }
    public Long getVersion() { return version; }
    public Instant getDeletedAt() { return deletedAt; }
}

