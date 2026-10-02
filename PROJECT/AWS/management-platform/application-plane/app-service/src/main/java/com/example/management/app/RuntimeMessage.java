package com.example.management.app;

import com.example.management.agent.GrdcClient;
import com.example.management.agent.AgentProperties;
import com.alibaba.nacos.api.config.listener.Listener;

import java.util.concurrent.Executor;
import java.util.Properties;
import java.io.StringReader;

import org.springframework.stereotype.Component;
import jakarta.annotation.PostConstruct;

@Component
public class RuntimeMessage {
  private volatile String message = "Hello from management-platform";
  private final GrdcClient grdc;
  private final AgentProperties p;

  public RuntimeMessage(GrdcClient g, AgentProperties p) {
    grdc = g;
    this.p = p;
  }

  @PostConstruct
  void start() throws Exception {
    String id = p.service + ".properties";
    String data = grdc.config().getConfigAndSignListener(id, p.group, 3000, new Listener() {
      public Executor getExecutor() {
        return null;
      }

      public void receiveConfigInfo(String v) {
        update(v);
      }
    });
    update(data);
  }

  private void update(String text) {
    if (text == null) return;
    try {
      var props = new Properties();
      props.load(new StringReader(text));
      message = props.getProperty("message", message);
    } catch (Exception ignored) {/* Keep last valid value. */}
  }

  public String message() {
    return message;
  }
}
