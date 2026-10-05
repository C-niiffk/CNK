package com.example.management.agent;

import java.util.*;
import java.net.InetAddress;

import jakarta.annotation.*;
import com.alibaba.nacos.api.*;
import com.alibaba.nacos.api.config.ConfigService;
import com.alibaba.nacos.api.naming.NamingService;
import com.alibaba.nacos.api.naming.pojo.Instance;
import com.alibaba.nacos.api.exception.NacosException;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.boot.availability.*;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;


@Component
public class GrdcClient {
  private static final org.slf4j.Logger log = org.slf4j.LoggerFactory.getLogger(GrdcClient.class);
  private final AgentProperties p;
  private final List<Endpoint> endpoints = new ArrayList<>();
  private volatile Instance self;
  private volatile boolean closing;

  private static final class Endpoint {
    final String address;
    NamingService naming;
    ConfigService config;

    Endpoint(String address) {
      this.address = address;
    }
  }

  public GrdcClient(AgentProperties p) {
    this.p = p;
  }

  @PostConstruct
  public void open() {
    for (String address : new LinkedHashSet<>(List.of(p.serverAddr, p.secondaryAddr)))
      if (!address.isBlank()) endpoints.add(new Endpoint(address));
    reconnect();
  }

  @Scheduled(fixedDelay = 10000)
  public synchronized void reconnect() {
    if (closing) return;
    for (Endpoint e : endpoints)
      try {
        Properties x = new Properties();
        x.setProperty("serverAddr", e.address);
        x.setProperty("username", p.username);
        x.setProperty("password", p.password);
        x.setProperty("namespace", p.namespace);
        x.setProperty("nacos.remote.client.grpc.timeout", "3000");
        if (e.naming == null) e.naming = NacosFactory.createNamingService(x);
        if (e.config == null) e.config = NacosFactory.createConfigService(x);
        if (self != null) e.naming.registerInstance(p.service, p.group, self);
      } catch (Exception failure) {
        log.warn("Registry endpoint temporarily unavailable: {}", e.address);
      }
  }

  @EventListener(ApplicationReadyEvent.class)
  public void register() throws Exception {
    if (!p.register) return;
    Instance i = new Instance();
    i.setIp(p.ip.isBlank() ? InetAddress.getLocalHost().getHostAddress() : p.ip);
    i.setPort(p.port);
    i.setClusterName(p.site);
    i.setEphemeral(true);
    i.setMetadata(Map.of("site", p.site, "runtime", System.getenv().getOrDefault("RUNTIME", "jvm")));
    self = i;
    reconnect();
  }

  @EventListener
  public void availability(AvailabilityChangeEvent<?> event) {
    if (event.getState() == ReadinessState.REFUSING_TRAFFIC) close();
  }

  public List<Instance> instances(String service) throws NacosException {
    return collect(service, true);
  }

  public List<Instance> allInstances(String service) throws NacosException {
    return collect(service, false);
  }

  private synchronized List<Instance> collect(String service, boolean healthy) throws NacosException {
    Map<String, Instance> result = new LinkedHashMap<>();
    boolean reached = false;
    for (Endpoint e : endpoints)
      try {
        if (e.naming == null) continue;
        var values = healthy ? e.naming.selectInstances(service, p.group, true) : e.naming.getAllInstances(service, p.group);
        reached = true;
        for (Instance i : values) result.put(i.getIp() + ":" + i.getPort(), i);
      } catch (NacosException unavailable) {
        log.debug("Registry read failed: {}", e.address);
      }
    if (!reached) throw new NacosException(500, "All registry endpoints unavailable");
    return List.copyOf(result.values());
  }

  public synchronized List<String> services() throws NacosException {
    Set<String> result = new TreeSet<>();
    boolean reached = false;
    for (Endpoint e : endpoints)
      try {
        if (e.naming != null) {
          result.addAll(e.naming.getServicesOfServer(1, 100, p.group).getData());
          reached = true;
        }
      } catch (NacosException ignored) {
      }
    if (!reached) throw new NacosException(500, "All registry endpoints unavailable");
    return List.copyOf(result);
  }

  public synchronized String read(String id) throws NacosException {
    boolean reached = false;
    for (Endpoint e : endpoints)
      try {
        if (e.config != null) {
          String v = e.config.getConfig(id, p.group, 2000);
          reached = true;
          if (v != null) return v;
        }
      } catch (NacosException ignored) {
      }
    if (!reached) throw new NacosException(500, "All config endpoints unavailable");
    return null;
  }

  /**
   * True means all configured stores acknowledged; caller persists desired configuration first.
   */
  public synchronized boolean publish(String id, String body, String type) {
    boolean all = !endpoints.isEmpty();
    for (Endpoint e : endpoints)
      try {
        if (e.config == null) {
          all = false;
          continue;
        }
        String old = e.config.getConfig(id, p.group, 2000);
        if (!Objects.equals(old, body) && !e.config.publishConfig(id, p.group, body, type)) all = false;
      } catch (NacosException ignored) {
        all = false;
      }
    return all;
  }

  public String group() {
    return p.group;
  }

  @PreDestroy
  public synchronized void close() {
    if (closing) return;
    closing = true;
    for (Endpoint e : endpoints) {
      try {
        if (e.naming != null && self != null) e.naming.deregisterInstance(p.service, p.group, self);
      } catch (Exception ignored) {
      }
      try {
        if (e.naming != null) e.naming.shutDown();
      } catch (Exception ignored) {
      }
      try {
        if (e.config != null) e.config.shutDown();
      } catch (Exception ignored) {
      }
    }
    self = null;
  }
}
