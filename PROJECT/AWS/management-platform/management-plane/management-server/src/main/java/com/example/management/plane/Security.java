package com.example.management.plane;

import com.example.management.common.Tokens;
import org.springframework.context.annotation.*;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;

@Configuration
@EnableMethodSecurity
public class Security {
  @Bean
  SecurityFilterChain chain(HttpSecurity http) throws Exception {
    return http.csrf(c -> c.disable()).sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
      .authorizeHttpRequests(a -> a.requestMatchers("/", "/index.html", "/ui.js", "/style.css", "/api/auth/token", "/actuator/health/**", "/actuator/prometheus").permitAll()
        .requestMatchers("/api/**").hasAnyRole("ADMIN", "VIEWER").anyRequest().denyAll())
      .oauth2ResourceServer(o -> o.jwt(j -> j.jwtAuthenticationConverter(Tokens::authentication))).build();
  }
}
