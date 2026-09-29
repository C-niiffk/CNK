package com.example.management.app;

import com.example.management.agent.*;

import java.net.*;
import java.net.http.*;
import java.time.Duration;
import java.util.*;

import org.springframework.stereotype.Component;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;

@Component
public class CrossSystemClient {
  private final GrdcClient grdc;
  private final AgentProperties p;
  private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(2)).build();

  public CrossSystemClient(GrdcClient g, AgentProperties p) {
    grdc = g;
    this.p = p;
  }

  public Map<String, Object> hello(String target, String token) throws Exception {
    if (!Set.of("app1", "app2").contains(target))
      throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "Unknown target");
    var sites = SiteSelector.candidates(p.site, grdc.instances(target), grdc.instances("gateway"), p.gateways.keySet());
    for (String site : sites) {
      // Retry only this idempotent GET; never silently retry a business write.
      try {
        var request = HttpRequest.newBuilder(URI.create(p.gateways.get(site) + "/api/" + target + "/hello")).timeout(Duration.ofSeconds(4)).header("Authorization", "Bearer " + token).GET().build();
        var response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() >= 500) continue;
        if (response.statusCode() != 200)
          throw new ResponseStatusException(HttpStatus.valueOf(response.statusCode()), "Remote gateway rejected request");
        return Map.of("caller", p.service, "callerSite", p.site, "selectedSite", site, "target", target, "body", response.body());
      } catch (java.io.IOException e) {/* Try the next healthy site for this GET only. */}
    }
    throw new ResponseStatusException(HttpStatus.SERVICE_UNAVAILABLE, "No healthy site for " + target);
  }
}
