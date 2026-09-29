package com.example.management.app;

import com.example.management.common.Tokens;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.web.SecurityFilterChain;

@Configuration
public class AppSecurity {
  @Bean
  SecurityFilterChain security(HttpSecurity h) throws Exception {
    return h.csrf(c->c.disable()).sessionManagement(s->s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
      .authorizeHttpRequests(a->a.requestMatchers("/actuator/health/**", "/actuator/prometheus")
        .permitAll().anyRequest().authenticated()).oauth2ResourceServer(o->o.jwt(
          j->j.jwtAuthenticationConverter(Tokens::authentication)
      )).build();
  }
}
