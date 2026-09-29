package com.example.management.gateway;

import com.example.management.common.*;
import org.springframework.stereotype.Component;
import org.springframework.cloud.gateway.filter.*;
import org.springframework.cloud.gateway.route.Route;

import static org.springframework.cloud.gateway.support.ServerWebExchangeUtils.GATEWAY_ROUTE_ATTR;

import org.springframework.core.Ordered;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.http.HttpStatus;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
import org.springframework.data.redis.core.ReactiveStringRedisTemplate;
import org.springframework.data.redis.core.script.RedisScript;
import reactor.core.publisher.Mono;

import java.util.*;

@Component
public class PolicyFilter implements GlobalFilter, Ordered {
  private final PolicyCache cache;
  private final ReactiveStringRedisTemplate redis;
  private final RedisScript<Long> script = RedisScript.of("local n=redis.call('INCR',KEYS[1]);if n==1 then redis.call('EXPIRE',KEYS[1],2) end;return n", Long.class);

  public PolicyFilter(PolicyCache c, ReactiveStringRedisTemplate r) {
    cache = c;
    redis = r;
  }

  public int getOrder() {
    return -10;
  }

  public Mono<Void> filter(ServerWebExchange e, GatewayFilterChain chain) {
    Route route = e.getAttribute(GATEWAY_ROUTE_ATTR);
    ApiPolicy policy = route == null ? null : cache.get(route.getId());
    if (policy == null) return reject(e, HttpStatus.FORBIDDEN);
    return e.getPrincipal().cast(JwtAuthenticationToken.class).flatMap(user -> {
      var roles = new HashSet<>(Optional.ofNullable(user.getToken().getClaimAsStringList("roles")).orElse(List.of()));
      if (!policy.permits(user.getName(), roles, e.getRequest().getMethod().name()))
        return reject(e, HttpStatus.FORBIDDEN);
      String key = "limit:" + policy.service() + ":" + user.getName() + ":" + (System.currentTimeMillis() / 1000);
      return redis.execute(script, List.of(key)).single().flatMap(n -> n > policy.requestsPerSecond() ? reject(e, HttpStatus.TOO_MANY_REQUESTS) : chain.filter(e))
        .onErrorResume(org.springframework.data.redis.RedisConnectionFailureException.class, x -> reject(e, HttpStatus.SERVICE_UNAVAILABLE));
    });
  }

  private Mono<Void> reject(ServerWebExchange e, HttpStatus s) {
    e.getResponse().setStatusCode(s);
    return e.getResponse().setComplete();
  }
}
