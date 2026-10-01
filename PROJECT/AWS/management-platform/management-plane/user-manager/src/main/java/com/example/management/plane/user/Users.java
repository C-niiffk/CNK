package com.example.management.plane.user;

import org.springframework.data.jpa.repository.JpaRepository;

public interface Users extends JpaRepository<PlatformUser, String> {
}
