package com.example.management.app;

import com.example.management.agent.AgentProperties;
import com.example.management.agent.GrdcClient;
import org.springframework.stereotype.Component;

import java.io.StringReader;
import java.util.Properties;


@Component
public class RuntimeMessage {
  private volatile String message = "hello from management-platform";
  private final GrdcClient grdc;
  private final AgentProperties p;

  public RuntimeMessage(GrdcClient g, AgentProperties p) {
    grdc = g;
    this.p = p;
  }

  @org.springframework.scheduling.annotation.Scheduled(fixedDelay = 5000, initialDelay = 1000)
  void refresh() {
    try {
      update(grdc.read(p.service + ".properties"));
    } catch (Exception ignored) { /* Last known good config survives a registry outage. */ }
  }

  private void update(String text) {
    if (text == null) return;
    try {
      var props = new Properties();
      props.load(new StringReader(text));
      message = props.getProperty("message", message);
    } catch (Exception e) {
    }
  }

  public String message() {
    return message;
  }
}
