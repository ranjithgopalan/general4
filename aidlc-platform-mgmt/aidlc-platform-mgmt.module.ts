import { NgModule } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";

import { AidlcPlatformMgmtRoutingModule } from "./aidlc-platform-mgmt-routing.module";
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
import { LibraryDependencyGraphComponent } from "./views/library-dependency-graph.component";
import { AspDependenciesComponent } from "./views/asp-dependencies.component";

@NgModule({
  declarations: [AidlcPlatformMgmtComponent, DashboardComponent, GlobalLibraryComponent, LibraryDependencyGraphComponent, AspDependenciesComponent, KnowledgeBaseComponent, OnboardComponent, OntologyComponent, KnowledgeOntologyComponent, PipelineComponent, PipelineSummaryComponent, WorkspaceTraceabilityComponent],
  imports: [CommonModule, FormsModule, AidlcPlatformMgmtRoutingModule],
  // AidlcPlatformMgmtService + WorkspaceStore are providedIn:'root'; no module-level providers needed.
})
export class AidlcPlatformMgmtModule {}
