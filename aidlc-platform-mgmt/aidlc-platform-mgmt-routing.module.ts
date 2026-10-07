import { NgModule } from "@angular/core";
import { RouterModule, Routes } from "@angular/router";

import { AidlcPlatformMgmtComponent } from "./components/aidlc-platform-mgmt.component";
import { DashboardComponent } from "./views/dashboard.component";
import { GlobalLibraryComponent } from "./views/global-library.component";
import { KnowledgeBaseComponent } from "./views/knowledge-base.component";
import { OnboardComponent } from "./views/onboard.component";
import { PipelineComponent } from "./views/pipeline.component";
import { PipelineSummaryComponent } from "./views/pipeline-summary.component";
import { OntologyComponent } from "./views/ontology.component";
import { WorkspaceTraceabilityComponent } from "./views/workspace-traceability.component";
import { KnowledgeOntologyComponent } from "./views/knowledge-ontology.component";
import { AspDependenciesComponent } from "./views/asp-dependencies.component";

const routes: Routes = [
  {
    path: "",
    component: AidlcPlatformMgmtComponent,
    children: [
      { path: "", redirectTo: "dashboard", pathMatch: "full" },
      { path: "dashboard", component: DashboardComponent },
      { path: "global-library", component: GlobalLibraryComponent },
      { path: "knowledge-ontology", component: KnowledgeOntologyComponent },
      { path: "asp-dependencies", component: AspDependenciesComponent },
      // Legacy routes preserved for backward compatibility
      { path: "knowledge-base", component: KnowledgeOntologyComponent },
      { path: "ontology", component: KnowledgeOntologyComponent },
      { path: "onboard", component: OnboardComponent },
      { path: "pipeline", component: PipelineComponent },
      { path: "pipeline-summary", component: PipelineSummaryComponent },
      { path: "workspaces/:id/traceability", component: WorkspaceTraceabilityComponent },
    ],
  },
];

@NgModule({
  imports: [RouterModule.forChild(routes)],
  exports: [RouterModule],
})
export class AidlcPlatformMgmtRoutingModule {}
