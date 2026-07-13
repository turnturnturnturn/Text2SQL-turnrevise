package com.example.copilot.domain;

import jakarta.persistence.*;

@Entity
@Table(name = "customers")
public class Customer {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String name;
    private String city;
    private String region;

    public Long getId() { return id; }
    public String getName() { return name; }
    public String getRegion() { return region; }
}

