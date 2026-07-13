package com.example.copilot.domain;

import jakarta.persistence.*;
import java.math.BigDecimal;
import java.time.Instant;

@Entity
@Table(name = "products")
public class Product {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String sku;
    private String name;
    private BigDecimal price;
    private Integer stock;
    @Version
    private Long version;
    @Column(name = "deleted_at")
    private Instant deletedAt;

    public Long getId() { return id; }
    public String getName() { return name; }
    public BigDecimal getPrice() { return price; }
    public Integer getStock() { return stock; }
    public Long getVersion() { return version; }
    public boolean isDeleted() { return deletedAt != null; }
}
