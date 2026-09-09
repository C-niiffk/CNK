package com.example.batch;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.userdetails.User;
import org.springframework.security.core.userdetails.UserDetailsService;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.provisioning.InMemoryUserDetailsManager;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.csrf.CookieCsrfTokenRepository;


/** Login Verification**/
@Configuration
class LoginProcess {
  @Bean
  UserDetailsService users(@Value("${batch.ui-user}") String username,
                           @Value("${batch.ui-password}") String password) {
    if (username.isBlank() || password.length() < 12 ||
      password.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > 72) {
      throw new IllegalArgumentException("Invalid username or password");
    }
    var encoder = new BCryptPasswordEncoder();
    var user = User.withUsername(username)
      .password("{bcrypt}" + encoder.encode(password))
      .roles("OPERATOR")
      .build();
    return new InMemoryUserDetailsManager(user);
  }

  @Bean
  SecurityFilterChain security(HttpSecurity http,
                               @Value("${batch.secure-cookie:false}") boolean secureCookie) throws Exception {
    var csrf = new CookieCsrfTokenRepository();
    csrf.setCookieCustomizer(cookie -> cookie.secure(secureCookie).sameSite("Strict"));
    return http
      .sessionManagement(s->s.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
      .csrf(c->c.csrfTokenRepository(csrf))
      .authorizeHttpRequests(a->a.
        requestMatchers("/health", "/health/**").permitAll().
        anyRequest().authenticated())
      .httpBasic(Customizer.withDefaults())
      .headers(h->h.frameOptions(f->f.deny()))
      .build();
  }
}
