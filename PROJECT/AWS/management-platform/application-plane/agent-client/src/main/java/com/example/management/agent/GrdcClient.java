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
import org.springframework.stereotype.Component;

@Component
public class GrdcClient {
  private static final org.slf4j.Logger log=org.slf4j.LoggerFactory.getLogger(GrdcClient.class);
  private final AgentProperties p; private NamingService naming; private ConfigService config; private Instance self;
  public GrdcClient(AgentProperties p){this.p=p;}
  @PostConstruct void open() throws Exception {
    Properties x=new Properties(); x.setProperty("serverAddr",p.serverAddr);x.setProperty("username",p.username);x.setProperty("password",p.password);x.setProperty("namespace",p.namespace);
    naming=NacosFactory.createNamingService(x);config=NacosFactory.createConfigService(x);
  }
  @EventListener(ApplicationReadyEvent.class) public void register() throws Exception {
    if(!p.register)return;
    self=new Instance();self.setIp(p.ip.isBlank()?InetAddress.getLocalHost().getHostAddress():p.ip);self.setPort(p.port);
    self.setClusterName(p.site);self.setEphemeral(true);self.setMetadata(Map.of("site",p.site,"runtime",System.getenv().getOrDefault("RUNTIME","jvm")));
    naming.registerInstance(p.service,p.group,self);
  }
  @EventListener public void availability(AvailabilityChangeEvent<?> event) {
    if(event.getState()==ReadinessState.REFUSING_TRAFFIC) deregister();
  }
  public List<Instance> instances(String service) throws NacosException {return naming.selectInstances(service,p.group,true);}
  public List<Instance> allInstances(String service) throws NacosException {return naming.getAllInstances(service,p.group);}
  public List<String> services() throws NacosException {return naming.getServicesOfServer(1,100,p.group).getData();}
  public String read(String id) throws NacosException {return config.getConfig(id,p.group,3000);}
  public boolean publish(String id,String body,String type) throws NacosException {return config.publishConfig(id,p.group,body,type);}
  public ConfigService config(){return config;} public String group(){return p.group;}
  private void deregister() {
    if(self==null)return;
    try{naming.deregisterInstance(p.service,p.group,self);self=null;}
    catch(NacosException e){log.warn("Nacos deregistration unavailable; server will expire the disconnected instance");}
  }
  @PreDestroy void close() throws Exception {
    deregister();
    try{if(naming!=null)naming.shutDown();}finally{if(config!=null)config.shutDown();}
  }
}
