package com.example.management.common;
import java.time.Instant;
import java.util.*;
import javax.crypto.spec.SecretKeySpec;
import com.nimbusds.jose.jwk.source.ImmutableSecret;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.*;
import org.springframework.security.oauth2.jose.jws.MacAlgorithm;
import org.springframework.security.oauth2.jwt.*;
import org.springframework.security.oauth2.core.*;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.oauth2.server.resource.authentication.JwtAuthenticationToken;
@Configuration
public class Tokens {
  public static final String ISSUER="management-platform";
  @Bean SecretKeySpec signingKey(@Value("${platform.jwt-secret}") String secret) {
    byte[] bytes=Base64.getDecoder().decode(secret);
    if(bytes.length<32) throw new IllegalArgumentException("JWT_SECRET must be base64 of at least 32 random bytes");
    return new SecretKeySpec(bytes,"HmacSHA256");
  }
  @Bean JwtDecoder jwtDecoder(SecretKeySpec key) {
    var d=NimbusJwtDecoder.withSecretKey(key).macAlgorithm(MacAlgorithm.HS256).build();
    d.setJwtValidator(new DelegatingOAuth2TokenValidator<>(JwtValidators.createDefaultWithIssuer(ISSUER),
      new JwtClaimValidator<List<String>>("aud", a -> a!=null && a.contains("platform"))));
    return d;
  }
  @Bean JwtEncoder jwtEncoder(SecretKeySpec key) { return new NimbusJwtEncoder(new ImmutableSecret<>(key)); }
  public static JwtAuthenticationToken authentication(Jwt jwt) {
    var roles=jwt.getClaimAsStringList("roles");
    return new JwtAuthenticationToken(jwt,(roles==null?List.<String>of():roles).stream().map(r->new SimpleGrantedAuthority("ROLE_"+r)).toList());
  }
  public static String issue(JwtEncoder encoder,String user,Collection<String> roles) {
    var now=Instant.now();
    var claims=JwtClaimsSet.builder().issuer(ISSUER).audience(List.of("platform")).subject(user).issuedAt(now)
      .expiresAt(now.plusSeconds(900)).claim("roles",roles).id(UUID.randomUUID().toString()).build();
    return encoder.encode(JwtEncoderParameters.from(JwsHeader.with(MacAlgorithm.HS256).build(),claims)).getTokenValue();
  }
}
