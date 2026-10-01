package com.example.management.plane.user;

import jakarta.persistence.*;

@Entity
@Table(name = "platform_user")
public class PlatformUser {
  @Id
  public String username;
  @Column(name = "password_hash", nullable = false)
  public String passwordHash;
  @Column(nullable = false)
  public String roles;
  @Column(nullable = false)
  public boolean enabled = true;

  protected PlatformUser() {
  }

  public PlatformUser(String u, String p, String r) {
    username = u;
    passwordHash = p;
    roles = r;
  }
}
