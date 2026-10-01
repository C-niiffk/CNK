package com.example.management.plane.user;

import com.example.management.common.*;
import com.example.management.common.contract.*;
import com.example.management.common.contract.AuditLog;
import org.springframework.web.bind.annotation.*;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.http.*;
import jakarta.validation.*;
import jakarta.validation.constraints.*;

import java.util.*;
import java.security.Principal;

@RestController
public class UserController {
  private final Users users;
  private final JwtEncoder encoder;
  private final AuditLog audits;
  private final BCryptPasswordEncoder passwords = new BCryptPasswordEncoder(12);

  public UserController(Users u, JwtEncoder e, AuditLog a) {
    users = u;
    encoder = e;
    audits = a;
  }

  public record Login(@NotBlank String username, @NotBlank String password) {
  }

  public record UserInput(@Pattern(regexp = "[a-zA-Z0-9._-]{3,64}") String username,
                          @Size(min = 12, max = 72) String password, @NotEmpty Set<String> roles, boolean enabled) {
  }

  @PostMapping("/api/auth/token")
  public ResponseEntity<?> token(@Valid @RequestBody Login l) {
    var u = users.findById(l.username());
    if (u.isEmpty() || !u.get().enabled || !passwords.matches(l.password(), u.get().passwordHash))
      return ResponseEntity.status(401).body(Map.of("error", "Invalid credentials"));
    return ResponseEntity.ok().cacheControl(CacheControl.noStore()).body(Map.of("access_token", Tokens.issue(encoder, l.username(), Arrays.asList(u.get().roles.split(","))), "token_type", "Bearer", "expires_in", 900));
  }

  @GetMapping("/api/users")
  @PreAuthorize("hasRole('ADMIN')")
  public Object list() {
    return users.findAll().stream().map(u -> Map.of("username", u.username, "roles", u.roles, "enabled", u.enabled)).toList();
  }

  @PutMapping("/api/users")
  @PreAuthorize("hasRole('ADMIN')")
  public Object save(@Valid @RequestBody UserInput in, Principal p) {
    if (!Set.of("ADMIN", "VIEWER", "API").containsAll(in.roles()))
      throw new IllegalArgumentException("Roles: ADMIN, VIEWER, API");
    if (in.username().equals(p.getName()) && (!in.enabled() || !in.roles().contains("ADMIN")))
      throw new IllegalArgumentException("Cannot remove your own administrator access");
    var u = new PlatformUser(in.username(), passwords.encode(in.password()), String.join(",", in.roles()));
    u.enabled = in.enabled();
    users.save(u);
    audits.record(p.getName(), "USER_UPDATE", in.username());
    return Map.of("saved", true);
  }

  @GetMapping("/api/audit")
  @PreAuthorize("hasRole('ADMIN')")
  public Object audit() {
    return audits.recent();
  }
}
