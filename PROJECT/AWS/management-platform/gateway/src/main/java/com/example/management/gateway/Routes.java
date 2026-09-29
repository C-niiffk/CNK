package com.example.management.gateway;

import org.springframework.context.annotation.*;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.cloud.gateway.route.*;
import org.springframework.cloud.gateway.route.builder.RouteLocatorBuilder;

@Configuration
public class Routes {
  @Bean
  RouteLocator applicationRoutes(RouteLocatorBuilder b, @Value("${platform.routes.app1}") String a, @Value("${platform.routes.app2}") String c) {
    return b.routes().route("app1", r -> r.path("/api/app1/**").filters(f -> f.stripPrefix(2)).uri(a))
      .route("app2", r -> r.path("/api/app2/**").filters(f -> f.stripPrefix(2)).uri(c)).build();
  }
}
