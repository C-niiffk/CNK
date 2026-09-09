package com.example.batch;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.context.annotation.Import;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import org.springframework.test.web.servlet.MockMvc;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.Mockito.when;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.csrf;
import static org.springframework.security.test.web.servlet.request.SecurityMockMvcRequestPostProcessors.httpBasic;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@WebMvcTest(FrontendController.class)
@Import(LoginProcess.class)
@TestPropertySource(properties = {"batch.ui-user=operator", "batch.ui-password=local-test-password"})
class FrontendControllerTest {
  @Autowired MockMvc mvc;
  @MockitoBean DBUtils repository;
  @MockitoBean BackendClient backend;

  @Test
  void protectsReadWithoutCredentials() throws Exception {
    mvc.perform(get("/api/jobs")).andExpect(status().isUnauthorized());
  }

  @Test
  void basicAuthenticationDoesNotBypassCsrf() throws Exception {
    mvc.perform(post("/api/jobs")
        .with(httpBasic("operator", "local-test-password"))
        .contentType(MediaType.APPLICATION_FORM_URLENCODED))
      .andExpect(status().isForbidden());
  }

  @Test
  void rejectsDuplicateFormFields() throws Exception {
    mvc.perform(post("/api/jobs").with(csrf())
        .with(httpBasic("operator", "local-test-password"))
        .contentType(MediaType.APPLICATION_FORM_URLENCODED)
        .param("app", "app1", "app2"))
      .andExpect(status().isBadRequest());
  }

  @Test
  void forwardsBackendConflictWithAuthenticationAndCsrf() throws Exception {
    when(backend.submit(anyMap())).thenReturn(ResponseEntity.status(409)
      .body("{\"error\":\"requestKey already used\"}"));
    mvc.perform(post("/api/jobs").with(csrf())
        .with(httpBasic("operator", "local-test-password"))
        .contentType(MediaType.APPLICATION_FORM_URLENCODED)
        .param("app", "app1").param("type", "SH").param("name", "reconcile")
        .param("businessDate", "2026-09-08").param("requestKey", "request_0001"))
      .andExpect(status().isConflict());
  }
}
