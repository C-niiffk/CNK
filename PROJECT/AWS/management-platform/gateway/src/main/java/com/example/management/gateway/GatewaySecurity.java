package com.example.management.gateway;

import com.example.management.common.Tokens;
import org.springframework.context.annotation.*;
import org.springframework.security.config.web.server.ServerHttpSecurity;
import org.springframework.security.web.server.SecurityWebFilterChain;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.security.web.server.context.NoOpServerSecurityContextRepository;
import reactor.core.publisher.Mono;

@Configuration
public class GatewaySecurity {
  @Bean
  ReactiveJwtDecoder reactiveDecoder(JwtDecoder decoder) {
    return token -> Mono.fromCallable(() -> decoder.decode(token));
  }

  @Bean
  SecurityWebFilterChain security(ServerHttpSecurity h) {
    return h.csrf(c -> c.disable()).securityContextRepository(NoOpServerSecurityContextRepository.getInstance())
      .authorizeExchange(a -> a.pathMatchers("/actuator/health/**", "/actuator/prometheus").permitAll().anyExchange().authenticated())
      .oauth2ResourceServer(o -> o.jwt(j -> j.jwtAuthenticationConverter(jwt -> Mono.just(Tokens.authentication(jwt))))).build();
  }
}
