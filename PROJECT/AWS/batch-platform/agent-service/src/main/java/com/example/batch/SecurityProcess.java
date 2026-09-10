package com.example.batch;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.List;

@Configuration
class SecurityProcess {
  @Bean
  SecurityFilterChain security(HttpSecurity http,
                               @Value("${batch.internal-token}") String token) throws Exception {
    if (token.length() < 32) {
      throw new IllegalArgumentException("INTERNAL_TOKEN must contain at least 32 characters");
    }
    byte[] expected = ("Bearer " + token).getBytes(StandardCharsets.UTF_8);
    // 不註冊為 Servlet Filter Bean，避免同一個 filter 被容器和 Security 執行兩次。
    var bearerFilter = new OncePerRequestFilter() {
      @Override
      protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response,
                                      FilterChain chain) throws ServletException, IOException {
        String header = request.getHeader("Authorization");
        if (header != null && MessageDigest.isEqual(expected, header.getBytes(StandardCharsets.UTF_8))) {
          var context = SecurityContextHolder.createEmptyContext();
          context.setAuthentication(UsernamePasswordAuthenticationToken.authenticated(
            "internal-service", null, List.of(new SimpleGrantedAuthority("ROLE_INTERNAL"))));
          SecurityContextHolder.setContext(context);
        }
        chain.doFilter(request, response);
      }
    };

    return http
      // 內部 API 只接受顯式 Bearer header，不接受 cookie 或 Basic 登入。
      .csrf(c -> c.disable())
      .sessionManagement(s -> s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
      .authorizeHttpRequests(a -> a
        .requestMatchers("/health", "/health/**").permitAll()
        .anyRequest().authenticated())
      .exceptionHandling(e -> e.authenticationEntryPoint((request, response, error) -> {
        response.setStatus(401);
        response.setContentType("application/json");
        response.getWriter().write("{\"error\":\"Unauthorized\"}");
      }))
      .addFilterBefore(bearerFilter, UsernamePasswordAuthenticationFilter.class)
      .build();
  }
}
