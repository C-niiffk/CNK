package com.example.batch;

import org.springframework.context.annotation.Bean;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;

public class LoginProcess {
  @Bean
  UserDetailsService users() {
    var encoder = new BCryptPasswordEncoder();
    return new InMemoryUserDetailsManager(User.withUsername(VerifyProcess.env("TEST", "operator"))
      .password("{bcrypt}"+encoder.encode(VerifyProcess.required("123456"))).roles("OPERATOR").build());
  }
  @Bean
  SecurityFilterChain security(HttpSecurity http) throws Exception {
    return http.csrf(c->c.disable()).sessionManagement(s->s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
      .authorizeHttpRequests(a->a.requestMatchers("/health").permitAll().anyRequest().authenticated())
      .httpBasic(Customizer.withDefaults()).headers(h->h.frameOptions(f->f.deny())).build();
  }
}
