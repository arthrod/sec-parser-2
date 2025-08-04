from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, cast

import nupunkt

from sec_parser.processing_engine.html_tag import HtmlTag
from sec_parser.processing_steps.abstract_classes.abstract_element_batch_processing_step import (
    AbstractElementBatchProcessingStep,
)
from sec_parser.semantic_elements.semantic_elements import (
    IrrelevantElement,
    TextElement,
)

if TYPE_CHECKING:  # pragma: no cover
    from sec_parser.processing_steps.abstract_classes.processing_context import (
        ElementProcessingContext,
    )
    from sec_parser.semantic_elements.abstract_semantic_element import (
        AbstractSemanticElement,
    )


class EnhancedTextElementMerger(AbstractElementBatchProcessingStep):
    """Enhanced TextElementMerger that uses nupunkt for sentence-boundary-aware merging.

    Only merges adjacent TextElements if they belong to the same sentence according to
    nupunkt's sentence boundary detection. This prevents inappropriate merging across
    sentence boundaries while still fixing formatting artifacts within sentences.

    Example of what gets merged:
        <span>Property and equipment, net, co</span>
        <span>nsisted of the following (in millions):</span>
        -> These belong to the same sentence, so they get merged.

    Example of what doesn't get merged:
        <span>This is the first sentence.</span>
        <span>This is the second sentence.</span>
        -> These are separate sentences, so they remain separate.
    """

    def __init__(self) -> None:
        super().__init__()
        # Initialize nupunkt sentence tokenizer once for efficiency
        self._sentence_tokenizer = nupunkt.PunktSentenceTokenizer()

    def _process_elements(
        self,
        elements: list[AbstractSemanticElement],
        context: ElementProcessingContext,
    ) -> list[AbstractSemanticElement]:
        result: deque[AbstractSemanticElement | None] = deque(elements)
        batch_indices: list[list[int]] = [[]]

        # Group adjacent TextElements into batches
        for i, element in enumerate(elements):
            if isinstance(element, TextElement):
                batch_indices[-1].append(i)
            elif batch_indices[-1] and not isinstance(element, IrrelevantElement):
                batch_indices.append([])

        # Process each batch with sentence-boundary-aware merging
        for indices in batch_indices:
            if len(indices) <= 1:
                continue

            # Get the TextElements in this batch
            text_elements = [
                cast("TextElement", result[i]) for i in indices if result[i] is not None
            ]

            # Apply sentence-boundary-aware merging
            merged_groups = self._group_by_sentence_boundaries(text_elements)

            # Replace the original elements with merged groups
            for group_idx, group in enumerate(merged_groups):
                if len(group) > 1:
                    # Merge elements in this group
                    merged_element = self._merge(cast("list[AbstractSemanticElement]", group))
                    if group_idx == 0:
                        # Place the first merged group at the original position
                        result[indices[0]] = merged_element
                    # Insert additional merged groups
                    # For simplicity, we'll place them at subsequent positions
                    # This maintains the relative order
                    elif group_idx < len(indices):
                        result[indices[group_idx]] = merged_element
                    else:
                        # If we have more groups than original positions,
                        # we need to handle this case (shouldn't happen often)
                        result[indices[-1]] = merged_element
                # Single element group - keep as is
                elif group_idx == 0:
                    result[indices[0]] = group[0]
                elif group_idx < len(indices):
                    result[indices[group_idx]] = group[0]

            # Mark remaining positions as None if we have fewer groups than indices
            for i in range(len(merged_groups), len(indices)):
                result[indices[i]] = None

        return [element for element in result if element is not None]

    def _group_by_sentence_boundaries(
        self, text_elements: list[TextElement],
    ) -> list[list[TextElement]]:
        """Group adjacent TextElements by sentence boundaries using nupunkt.

        Returns a list of groups, where each group contains TextElements that
        belong to the same sentence.
        """
        if len(text_elements) <= 1:
            return [text_elements]

        # Extract text content from all elements
        texts = []
        for element in text_elements:
            text = element.html_tag.text or "" if hasattr(element, "html_tag") and element.html_tag else ""
            texts.append(text)

        # Combine all texts to analyze sentence boundaries
        combined_text = " ".join(texts)

        # Use nupunkt to detect sentence boundaries
        try:
            sentences = self._sentence_tokenizer.tokenize(combined_text)
        except Exception:
            # Fallback: if nupunkt fails, treat as single group (original behavior)
            return [text_elements]

        if len(sentences) <= 1:
            # All elements belong to the same sentence
            return [text_elements]

        # Map each TextElement to its sentence
        groups: list[list[TextElement]] = []
        current_group: list[TextElement] = []

        # Track position in combined text
        text_position = 0
        sentence_boundaries = []

        # Calculate sentence boundary positions in the combined text
        for sentence in sentences:
            start_pos = combined_text.find(sentence.strip(), text_position)
            if start_pos != -1:
                end_pos = start_pos + len(sentence.strip())
                sentence_boundaries.append((start_pos, end_pos))
                text_position = end_pos

        # Assign each TextElement to a sentence group
        element_position = 0
        current_sentence_idx = 0

        for i, (element, text) in enumerate(zip(text_elements, texts)):
            element_start = element_position
            element_end = element_position + len(text)

            # Find which sentence this element belongs to
            element_sentence_idx = current_sentence_idx
            for j, (sent_start, sent_end) in enumerate(sentence_boundaries):
                if element_start >= sent_start and element_start < sent_end:
                    element_sentence_idx = j
                    break

            # If we've moved to a new sentence, start a new group
            if element_sentence_idx > current_sentence_idx:
                if current_group:
                    groups.append(current_group)
                current_group = [element]
                current_sentence_idx = element_sentence_idx
            else:
                current_group.append(element)

            # Update position (add 1 for the space we added when joining)
            element_position = element_end + (1 if i < len(texts) - 1 else 0)

        # Add the last group
        if current_group:
            groups.append(current_group)

        # Fallback: if grouping failed, return each element as its own group
        if not groups:
            return [[element] for element in text_elements]

        return groups

    @classmethod
    def _merge(
        cls,
        elements: list[AbstractSemanticElement],
    ) -> AbstractSemanticElement:
        """Merge multiple TextElements into a single TextElement.

        This method is identical to the original TextElementMerger._merge()
        to maintain compatibility and preserve existing behavior.
        """
        new_tag = HtmlTag.wrap_tags_in_new_parent(
            "sec-parser-merged-text",
            [e.html_tag for e in elements],
        )
        merged_processing_log = elements[0].processing_log.copy()
        # After merging, we retain the processing log of the first element and drop the logs of the others.
        # This is because the merged text element now represents a single entity, and we want to avoid
        # log duplication or confusion about which part of the merged text the logs refer to.
        dropped_logs = [e.processing_log for e in elements[1:]]
        if any(dropped_logs):
            merged_processing_log.add_item(
                message="Merged multiple TextElements using sentence-boundary-aware logic. Processing logs from subsequent elements are dropped.",
                log_origin=cls.__name__,
            )
        return TextElement(
            new_tag,
            processing_log=merged_processing_log,
            log_origin=cls.__name__,
        )
