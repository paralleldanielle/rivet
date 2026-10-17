# Conformance map

Every scenario in spec §21 maps to at least one black-box test. These tests create temporary Git repositories and call `plugins/rivet/bin/rivet` as a real subprocess. C56 also patches the engine in-process to simulate a concurrent edit. Further regression tests for known failure modes sit next to these tests, in the same files.

Run with `python3 -m unittest discover -s tests`.

| Spec ID | Test |
| --- | --- |
| C01 | `test_config.py` ConfigurationTests.test_c01_init_without_configuration_uses_restricted_defaults |
| C02 | `test_config.py` ConfigurationTests.test_c02_init_again_preserves_configuration_and_ignore_file<br>`test_config.py` ConfigurationTests.test_c02_existing_ignore_file_without_runtime_rule_is_kept_and_reported |
| C03 | `test_config.py` ConfigurationTests.test_c03_preview_creates_no_files_or_runtime_state |
| C04 | `test_config.py` ConfigurationTests.test_c04_write_orchestrated_configuration |
| C05 | `test_config.py` ConfigurationTests.test_c05_rejects_invalid_configuration<br>`test_config.py` ConfigurationTests.test_c05_context_dir_symlink_escape_rejected |
| C06 | `test_config.py` ConfigurationTests.test_c06_profile_limits |
| C07 | `test_config.py` ConfigurationTests.test_c07_replace_requires_flag_and_is_locked_by_admitted_work |
| C08 | `test_config.py` ConfigurationTests.test_c08_unadmitted_proposal_does_not_lock_configuration |
| C09 | `test_config.py` ConfigurationTests.test_c09_model_allow_list_semantics |
| C10 | `test_config.py` ConfigurationTests.test_c10_admission_allocates_whole_configured_limit |
| C11 | `test_lifecycle.py` LifecycleTests.test_c11_incomplete_dependencies_block_admission_and_ready_is_read_only |
| C12 | `test_lifecycle.py` LifecycleTests.test_c12_readmission_cannot_change_the_contract |
| C13 | `test_concepts.py` ConceptTests.test_c13_fingerprint_is_sha256_of_exact_bytes_and_creates_no_state |
| C14 | `test_concepts.py` ConceptTests.test_c14_rejects_unsafe_concept_references |
| C15 | `test_concepts.py` ConceptTests.test_c15_preview_is_read_only |
| C16 | `test_concepts.py` ConceptTests.test_c16_any_bound_change_invalidates_confirmation<br>`test_concepts.py` ConceptTests.test_c16_initializing_after_preview_invalidates_confirmation |
| C17 | `test_concepts.py` ConceptTests.test_c17_matching_confirmation_starts_exactly_one_running_item |
| C18 | `test_concepts.py` ConceptTests.test_c18_direct_concept_start_without_preview |
| C19 | `test_concepts.py` ConceptTests.test_c19_concept_start_needs_paths_and_acceptance_before_creating_work |
| C20 | `test_concepts.py` ConceptTests.test_c20_incomplete_dependency_leaves_ready_unadmitted_confirmed_proposal |
| C21 | `test_concepts.py` ConceptTests.test_c21_propose_cannot_bypass_concept_pinning |
| C22 | `test_concepts.py` ConceptTests.test_c22_every_risk_level_completes_without_a_final_approver |
| C23 | `test_lifecycle.py` LifecycleTests.test_c23_concurrent_claims_have_exactly_one_winner |
| C24 | `test_lifecycle.py` LifecycleTests.test_c24_claim_preconditions |
| C25 | `test_lifecycle.py` LifecycleTests.test_c25_wrong_or_expired_epoch_cannot_mutate |
| C26 | `test_lifecycle.py` LifecycleTests.test_c26_authenticated_calls_renew_and_reads_do_not |
| C27 | `test_lifecycle.py` LifecycleTests.test_c27_stale_expected_version_cannot_mutate |
| C28 | `test_lifecycle.py` LifecycleTests.test_c28_first_claim_requires_unchanged_worktree_and_reclaim_keeps_baseline |
| C29 | `test_lifecycle.py` LifecycleTests.test_c29_pause_keeps_the_lease |
| C30 | `test_lifecycle.py` LifecycleTests.test_c30_pause_release_reclaim_preserves_contract_and_counters |
| C31 | `test_lifecycle.py` LifecycleTests.test_c31_abandon_removes_lease_without_reverting_code |
| C32 | `test_lifecycle.py` LifecycleTests.test_c32_followups_are_quarantined |
| C33 | `test_scope.py` ScopeTests.test_c33_c34_c35_preexisting_edits_are_visible_but_not_attributed |
| C34 | `test_scope.py` ScopeTests.test_c33_c34_c35_preexisting_edits_are_visible_but_not_attributed |
| C35 | `test_scope.py` ScopeTests.test_c33_c34_c35_preexisting_edits_are_visible_but_not_attributed |
| C36 | `test_scope.py` ScopeTests.test_c36_prefixes_are_literal_and_renames_report_both_sides |
| C37 | `test_scope.py` ScopeTests.test_c37_committing_an_out_of_scope_edit_does_not_hide_it |
| C38 | `test_scope.py` ScopeTests.test_c38_deletions_mode_and_symlink_target_changes |
| C39 | `test_scope.py` ScopeTests.test_c39_new_context_draft_is_exempt_but_existing_untracked_note_is_not<br>`test_scope.py` ScopeTests.test_c39_custom_context_dir_and_committed_doc |
| C40 | `test_scope.py` ScopeTests.test_c40_editing_admitted_concept_blocks_completion_inside_allowed_paths |
| C41 | `test_scope.py` ScopeTests.test_c41_corrupt_baseline_and_broken_ancestry_are_unavailable_never_clean<br>`test_scope.py` ScopeTests.test_c41_history_rewrite_makes_scope_unavailable<br>`test_scope.py` ScopeTests.test_c41_changed_writer_path_is_unavailable |
| C42 | `test_scope.py` ScopeTests.test_c42_runtime_and_ignored_files_stay_out_of_measurement |
| C43 | `test_scope.py` ScopeTests.test_c43_control_file_changes_outside_scope_are_violations |
| C44 | `test_scope.py` ScopeTests.test_c44_parent_scope_does_not_look_inside_submodules |
| C45 | `test_evidence.py` EvidenceTests.test_c45_nonzero_exit_is_a_recorded_measured_failure |
| C46 | `test_evidence.py` EvidenceTests.test_c46_pass_and_timeout |
| C47 | `test_evidence.py` EvidenceTests.test_c47_launch_failure_records_nothing |
| C48 | `test_evidence.py` EvidenceTests.test_c48_verification_runs_in_the_writer_and_renews_the_lease |
| C49 | `test_evidence.py` EvidenceTests.test_c49_any_repository_change_invalidates_evidence |
| C50 | `test_evidence.py` EvidenceTests.test_c50_scope_exempt_draft_still_invalidates_evidence |
| C51 | `test_evidence.py` EvidenceTests.test_c51_measured_attribution_survives_later_assertions |
| C52 | `test_evidence.py` EvidenceTests.test_c52_batches_are_atomic |
| C53 | `test_evidence.py` EvidenceTests.test_c53_ordinary_evidence_cannot_forge_external_or_measured |
| C54 | `test_evidence.py` EvidenceTests.test_c54_finish_with_missing_criteria_reports_blockers_without_advancing |
| C55 | `test_evidence.py` EvidenceTests.test_c55_current_evidence_and_scope_complete_and_remove_the_lease |
| C56 | `test_evidence.py` EvidenceTests.test_c56_changes_during_completion_prevent_settlement |
| C58 | `test_evidence.py` EvidenceTests.test_c58_any_valid_pass_satisfies_a_criterion |
| C59 | `test_delegation.py` DelegationTests.test_c59_restricted_rejects_dispatch_orchestrated_running_permits |
| C60 | `test_delegation.py` DelegationTests.test_c60_limit_two_allows_exactly_two_reservations |
| C61 | `test_delegation.py` DelegationTests.test_c61_failed_settlement_consumes_a_call_and_counters_never_refund |
| C62 | `test_delegation.py` DelegationTests.test_c62_settlement_needs_an_outstanding_reservation |
| C63 | `test_delegation.py` DelegationTests.test_c63_item_delegation_unavailable_at_zero_remaining |
| C64 | `test_delegation.py` DelegationTests.test_c64_gates_need_a_matching_approval |
| C65 | `test_delegation.py` DelegationTests.test_c65_consumption_is_single_use |
| C66 | `test_delegation.py` DelegationTests.test_c66_completion_is_not_publication_approval |
| C67 | `test_delegation.py` DelegationTests.test_c67_engine_side_rules |
| C68 | `test_external.py` ExternalTests.test_c68_declaration_validation |
| C69 | `test_external.py` ExternalTests.test_c69_unknown_name_and_malformed_sha |
| C70 | `test_external.py` ExternalTests.test_c70_published_ancestor_passes_after_the_ref_advances |
| C71 | `test_external.py` ExternalTests.test_c71_unrelated_commit_fails_and_every_declaration_is_required |
| C72 | `test_external.py` ExternalTests.test_c72_c73_parent_gitlink_must_match_and_submodule_production_is_accepted |
| C73 | `test_external.py` ExternalTests.test_c72_c73_parent_gitlink_must_match_and_submodule_production_is_accepted |
| C74 | `test_external.py` ExternalTests.test_c74_remote_rewrite_after_a_pass_blocks_completion |
| C75 | `test_external.py` ExternalTests.test_c75_external_checks_do_not_touch_checkouts_or_push |
| C76 | `test_recovery.py` DoctorTests.test_c76_c77_expired_and_ownerless_work_is_reported_and_repaired |
| C77 | `test_recovery.py` DoctorTests.test_c76_c77_expired_and_ownerless_work_is_reported_and_repaired |
| C78 | `test_recovery.py` DoctorTests.test_c78_repair_touches_only_lease_and_lifecycle_metadata |
| C79 | `test_recovery.py` WorktreeTests.test_c79_c81_isolated_writer_has_its_own_baseline_and_survives_reclaim |
| C80 | `test_recovery.py` WorktreeTests.test_c80_worktree_requires_untouched_item_and_committed_concept |
| C81 | `test_recovery.py` WorktreeTests.test_c79_c81_isolated_writer_has_its_own_baseline_and_survives_reclaim |
| C82 | `test_recovery.py` PortabilityTests.test_c82_checkpoint_round_trip_and_identical_rows_skipped |
| C83 | `test_recovery.py` PortabilityTests.test_c83_conflicts_and_deletions_abort_the_whole_import |
| C84 | `test_recovery.py` PortabilityTests.test_c84_event_only_import_cannot_change_canonical_state |
| C85 | `test_config.py` ConfigurationTests.test_c85_stale_configuration_schema_fails_closed<br>`test_config.py` ConfigurationTests.test_c85_stale_database_schema_fails_closed_and_names_runtime |
| C86 | `test_recovery.py` PortabilityTests.test_c86_corrupt_imported_baseline_is_never_clean |
| C87 | `test_recovery.py` InspectionTests.test_c87_compact_status_and_actionable_show |
| C88 | `test_recovery.py` PortabilityTests.test_c88_errors_are_json_never_tracebacks |
| C89 | `test_recovery.py` InspectionTests.test_c89_redirection_pauses_live_work_and_leaves_it_alone |

Adapter packaging (manifests, skills, generated roles, launcher) is covered by `test_adapters.py`, and `rivet bootstrap` by `test_bootstrap.py`.
